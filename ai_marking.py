import os
import logging
import base64
import json
import re
import io
import hashlib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Import providers
from anthropic import Anthropic

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from pdf2image import convert_from_bytes
    from PIL import Image
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# Register the HEIF opener so PIL can open HEIC files uploaded from iPhones/iPads.
# Broad except: pillow-heif can raise RuntimeError on some containers that lack
# libheif bindings. Treat any failure as "HEIF support unavailable" instead of
# letting it crash app startup.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except Exception as _heif_err:
    HEIF_AVAILABLE = False
    logger.warning(f"HEIF support unavailable: {_heif_err}")

# Provider and model configuration
PROVIDERS = {
    'anthropic': {
        'label': 'Anthropic',
        'models': {
            'claude-sonnet-4-6': 'Claude Sonnet 4.6',
            'claude-haiku-4-5-20251001': 'Claude Haiku 4.5',
        },
        'default': 'claude-sonnet-4-6',
    },
    'openai': {
        'label': 'OpenAI',
        'models': {
            'gpt-5.4': 'GPT-5.4',
            'gpt-5.4-mini': 'GPT-5.4 Mini',
        },
        'default': 'gpt-5.4',
    },
    'qwen': {
        'label': 'Qwen',
        'models': {
            'qwen3.6-plus-2026-04-02': 'Qwen 3.6 Plus',
            'qwen3.5-plus-2026-02-15': 'Qwen 3.5 Plus',
        },
        'default': 'qwen3.6-plus-2026-04-02',
    },
}


PROVIDER_KEY_MAP = {
    'anthropic': 'ANTHROPIC_API_KEY',
    'openai': 'OPENAI_API_KEY',
    'qwen': 'QWEN_API_KEY',
}


# Cheap per-provider model used for the small JSON-classification tasks —
# explain_criterion ("the idea" sentence) and evaluate_correction ("good"/
# "not_quite" judgement). Both are short pattern-matching tasks where the
# tier-2 model is plenty.
HELPER_MODELS = {
    'anthropic': 'claude-haiku-4-5-20251001',
    'openai': 'gpt-5.4-mini',
    'qwen': 'qwen3.5-plus-2026-02-15',
}


def _helper_model_for(provider, fallback):
    """Return the cheap helper model for `provider`, or `fallback` if the
    provider isn't in the cheap map (custom providers, future additions)."""
    return HELPER_MODELS.get(provider) or fallback


def _resolve_api_key(provider, session_keys=None):
    """Get API key from session keys → env vars → wizard-stored DB keys."""
    env_name = PROVIDER_KEY_MAP.get(provider)
    if session_keys and session_keys.get(provider):
        return session_keys[provider]
    env_val = os.getenv(env_name) if env_name else None
    if env_val:
        return env_val
    # Fall back to wizard-stored encrypted keys in DepartmentConfig
    try:
        from db import DepartmentConfig, _get_fernet
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{provider}').first()
        if cfg and cfg.value:
            f = _get_fernet()
            if f:
                try:
                    return f.decrypt(cfg.value.encode()).decode()
                except Exception:
                    pass
            return cfg.value
    except Exception:
        pass
    return None


def get_available_providers(session_keys=None):
    """Return dict of provider -> config for providers with API keys available."""
    available = {}
    if _resolve_api_key('anthropic', session_keys):
        available['anthropic'] = PROVIDERS['anthropic']
    if _resolve_api_key('openai', session_keys) and OPENAI_AVAILABLE:
        available['openai'] = PROVIDERS['openai']
    if _resolve_api_key('qwen', session_keys) and OPENAI_AVAILABLE:
        available['qwen'] = PROVIDERS['qwen']
    return available


def get_ai_client(provider, model=None, session_keys=None):
    """Get AI client for a provider. Returns (client, model_name, provider) or (None, None, None)."""
    prov_config = PROVIDERS.get(provider)
    if not prov_config:
        return None, None, None

    # Validate model choice, fall back to default
    valid_models = prov_config['models']
    if not model or model not in valid_models:
        model = prov_config['default']

    api_key = _resolve_api_key(provider, session_keys)
    if not api_key:
        return None, None, None

    if provider == 'anthropic':
        return Anthropic(api_key=api_key), model, 'anthropic'

    elif provider == 'openai':
        if not OPENAI_AVAILABLE:
            return None, None, None
        return OpenAI(api_key=api_key), model, 'openai'

    elif provider == 'qwen':
        if not OPENAI_AVAILABLE:
            return None, None, None
        client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        return client, model, 'qwen'

    return None, None, None


def convert_pdf_to_images(pdf_bytes, max_pages=10):
    """Convert PDF pages to base64-encoded JPEG images."""
    if not PDF2IMAGE_AVAILABLE:
        return []
    try:
        images = convert_from_bytes(pdf_bytes, first_page=1, last_page=max_pages)
        result = []
        for img in images:
            buf = io.BytesIO()
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            result.append(base64.b64encode(buf.read()).decode('utf-8'))
        return result
    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
        return []


def resize_image_for_ai(image_bytes, max_dimension=1200, quality=85):
    """Resize image to reduce payload size for AI APIs."""
    if not PDF2IMAGE_AVAILABLE:
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if w <= max_dimension and h <= max_dimension:
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=quality)
            return out.getvalue()
        ratio = min(max_dimension / w, max_dimension / h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=quality)
        return out.getvalue()
    except Exception:
        return image_bytes


def build_content_block(file_bytes):
    """Build API content block based on file type (PDF or image)."""
    if file_bytes[:5] == b'%PDF-':
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode('utf-8')
            }
        }

    # Detect image type
    if file_bytes[:3] == b'\xff\xd8\xff':
        media_type = "image/jpeg"
    elif file_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        media_type = "image/png"
    elif file_bytes[:4] in (b'GIF8',):
        media_type = "image/gif"
    elif file_bytes[:4] == b'RIFF' and len(file_bytes) > 12 and file_bytes[8:12] == b'WEBP':
        media_type = "image/webp"
    elif len(file_bytes) > 12 and file_bytes[4:8] == b'ftyp' and file_bytes[8:12] in (
        b'heic', b'heix', b'hevc', b'heim', b'heis', b'mif1', b'msf1', b'heif'
    ):
        # HEIC / HEIF (iPhone photos). Convert to JPEG so AI APIs accept it.
        if not HEIF_AVAILABLE or not PDF2IMAGE_AVAILABLE:
            logger.error("HEIC upload received but pillow-heif or Pillow is not installed")
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(file_bytes).decode('utf-8')
                }
            }
        try:
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            file_bytes = buf.getvalue()
            media_type = "image/jpeg"
        except Exception as e:
            logger.error(f"Failed to convert HEIC to JPEG: {e}")
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(file_bytes).decode('utf-8')
                }
            }
    else:
        # Default to PDF
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode('utf-8')
            }
        }

    resized = resize_image_for_ai(file_bytes)
    # resize_image_for_ai re-encodes as JPEG when Pillow is available. If the
    # returned bytes start with the JPEG magic, the content block's media_type
    # must match the actual bytes — otherwise Anthropic rejects a PNG-labelled
    # JPEG payload.
    if resized[:3] == b'\xff\xd8\xff':
        media_type = "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(resized).decode('utf-8')
        }
    }


def make_ai_api_call(client, model_name, provider, system_prompt, messages_content, max_tokens=32000):
    """Unified API call across providers."""
    if provider == 'anthropic':
        # Convert system_prompt to a cached block list. The system text is
        # identical across every student of the same assignment, so caching
        # it lets bulk marking pay full price only on the first student.
        # Below the cache minimum (~1024 tokens), Anthropic silently skips
        # caching, so this is safe even for tiny system prompts.
        system_blocks = (
            [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
            if system_prompt else None
        )
        # Use streaming to avoid 10-minute timeout on large requests
        with client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": messages_content}],
            system=system_blocks
        ) as stream:
            return stream.get_final_text()

    elif provider in ('openai', 'qwen'):
        openai_messages = []
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})

        user_content = []
        for item in messages_content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    user_content.append({"type": "text", "text": item.get('text', '')})
                elif item.get('type') == 'image':
                    image_data = item['source']['data']
                    media_type = item['source'].get('media_type', 'image/jpeg')
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_data}"}
                    })
                elif item.get('type') == 'document':
                    pdf_data = item['source']['data']
                    pdf_bytes = base64.b64decode(pdf_data)
                    pdf_images = convert_pdf_to_images(pdf_bytes, max_pages=10)
                    if pdf_images:
                        for page_num, img_b64 in enumerate(pdf_images, 1):
                            user_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                            })
                            user_content.append({"type": "text", "text": f"(PDF Page {page_num})"})
                    else:
                        user_content.append({"type": "text", "text": "[PDF document could not be converted to images]"})

        # Combine text and images for OpenAI format
        text_parts = [c.get('text', '') for c in user_content if c.get('type') == 'text']
        image_parts = [c for c in user_content if c.get('type') == 'image_url']

        if image_parts:
            content_list = []
            for text in text_parts:
                if text.strip():
                    content_list.append({"type": "text", "text": text})
            content_list.extend(image_parts)
            user_content = content_list
        else:
            user_content = [{"type": "text", "text": " ".join(text_parts)}]

        openai_messages.append({"role": "user", "content": user_content})

        # OpenAI GPT-5+ uses max_completion_tokens; Qwen uses max_tokens
        token_param = 'max_completion_tokens' if provider == 'openai' else 'max_tokens'
        response = client.chat.completions.create(
            model=model_name,
            messages=openai_messages,
            **{token_param: max_tokens}
        )
        return response.choices[0].message.content

    raise ValueError(f"Unknown provider: {provider}")


def parse_ai_response(response_text):
    """Parse AI response JSON, handling markdown fences, Qwen thinking text, and truncation."""
    if not response_text or not response_text.strip():
        return {'error': 'Empty response'}

    text = response_text.strip()

    # Strip Qwen <think>...</think> reasoning blocks before parsing
    text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()

    # Replace smart quotes with regular quotes (Qwen sometimes uses these)
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")

    # Strip markdown code fences — handle fences anywhere in text, not just start/end
    # Use greedy match inside fences so nested JSON objects are captured fully
    fence_match = re.search(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', text)
    if fence_match:
        text = fence_match.group(1)
    else:
        # Fallback: strip fences at boundaries
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)

    # Find the outermost JSON object
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        logger.warning(f"No JSON found in response (length={len(text)}). First 500 chars: {text[:500]}")
        return {'error': 'Could not parse response', 'raw': response_text}

    raw_json = json_match.group()

    # Attempt 1: direct parse
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        pass

    # Attempt 2: repair truncated JSON
    cleaned = raw_json.rstrip()
    quote_count = len(re.findall(r'(?<!\\)"', cleaned))
    if quote_count % 2 != 0:
        cleaned += '"'
    cleaned = re.sub(r',\s*"[^"]*"\s*:\s*"?[^"{}[\]]*$', '', cleaned)
    cleaned = re.sub(r',\s*$', '', cleaned)
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')
    cleaned += ']' * max(0, open_brackets) + '}' * max(0, open_braces)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 3: try each JSON object in response (Qwen sometimes adds extra text with braces)
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text):
        try:
            obj = json.loads(m.group())
            if 'questions' in obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    logger.warning(f"All parse attempts failed. First 500 chars: {text[:500]}")
    return {'error': 'Could not parse response', 'raw': response_text}


def _append_pages(content, label, pages):
    """Append one or more file pages to the content array."""
    content.append({"type": "text", "text": label})
    for i, page_bytes in enumerate(pages):
        content.append(build_content_block(page_bytes))
        if len(pages) > 1:
            content.append({"type": "text", "text": f"(Page {i + 1})"})


def _mark_anthropic_cache_breakpoint(content):
    """Tag the last block of `content` so Anthropic caches everything up to
    and including it. Safe to call regardless of provider — the OpenAI /
    Qwen adapters in make_ai_api_call read only `type` / `text` / `source`
    from each block and silently ignore cache_control.

    Cache TTL is ~5 minutes, so within a bulk-marking burst (the common
    teacher flow), the question paper + answer key + rubrics + system
    prompt only get billed at full rate on the first student; subsequent
    students read the cached prefix at 10% of the input-token cost.
    """
    if content:
        content[-1] = {**content[-1], 'cache_control': {'type': 'ephemeral'}}


# Shared block injected into every short-answer and rubrics marking prompt so
# the two feedback fields ("Feedback" and "Suggested Improvement") follow the
# same discipline regardless of marking format. Edit this constant to change
# the rules everywhere at once.
FEEDBACK_GENERATION_RULES = """FEEDBACK GENERATION RULES

FIELD NAMES

The two feedback fields are named:
  Feedback
  Suggested Improvement

Use these exact headers when reasoning about what to write. Do not use "What
Happened", "Next Time", or any other labels.


WHEN THE STUDENT IS FAR FROM THE CORRECT ANSWER

Before generating feedback for any criterion, assess the distance between the
student's answer and what was required:

  CLOSE   — Right idea, missed a detail or precise term. Most marks earned.
  PARTIAL — Some relevant content but significant gaps. Roughly half marks
            or fewer earned.
  FAR     — Answer mostly incorrect, missing, or fundamentally misunderstood
            the question. Very few or no marks earned.

Apply these rules by distance:

IF CLOSE:
Generate Feedback and Suggested Improvement as normal. Name the specific gap
precisely.

IF PARTIAL:
Focus BOTH lines on the single most important gap only — the one that accounts
for the most marks lost. Do not list multiple things that were missing.

IF FAR:
Do not list everything that was wrong. Identify the ONE foundational thing the
student would need to understand first before anything else makes sense. This
is the entry point — not the most obvious gap, but the one most upstream in
their reasoning.

Generate only:
  Feedback: One sentence naming that single foundational gap.
  Suggested Improvement: One thing to do or ask themselves, aimed at that
  gap only.


EXAMPLES FOR FAR DISTANCE

These examples show the difference between listing symptoms and naming the
foundational gap. Study the RIGHT examples carefully — note how short and
plain they are.

Science — student described unrelated cell activity instead of the stages of
mitosis:

WRONG (lists symptoms, too long):
Feedback: "Your answer described cell activity but wasn't structured around
the stages of mitosis — that sequence is the framework the whole answer hangs
on, and without it the other details have no place to sit."

RIGHT:
Feedback: "Your answer didn't follow the stages of mitosis — the question
needed that structure."
Suggested Improvement: "Write the stage names first, then build each point
around them."

Humanities SEQ — student wrote off-topic:

WRONG (vague, still too long):
Feedback: "Your answer didn't directly address what the question was asking
— everything else follows from getting that focus right first."

RIGHT:
Feedback: "Your answer didn't address the question being asked."
Suggested Improvement: "Underline the question's directive word before
writing — that word tells you what your answer needs to do."


WORD LIMITS

Feedback: maximum 20 words.
Suggested Improvement: maximum 20 words.

These limits apply at all distance levels — CLOSE, PARTIAL, and FAR alike.
The limit is absolute. One Feedback sentence. One Suggested Improvement
sentence. No exceptions."""


# Extra rules that apply ONLY to rubric / band-descriptor marking. The reference
# point in this mode is a band descriptor, not a specific correct answer — so
# the failure mode to defend against is the model paraphrasing the descriptor
# back at the student instead of naming the concrete quality difference.
# Injected into _build_rubrics_prompt() AFTER the shared rules above.
RUBRIC_FEEDBACK_RULES = """RUBRIC-BASED FEEDBACK RULES (applies on top of FEEDBACK GENERATION RULES)

The reference point is a band descriptor, not a specific correct answer.

Do NOT paraphrase the band descriptor in your feedback.
Do NOT tell the student which band they are in or which band they need to reach.

Instead, name the specific quality difference between what they actually wrote
and what a stronger response would do.

WRONG (this is the band descriptor in different words):
"Your response needs sustained analysis with integrated evidence to reach the
higher band."

WRONG (bands are for teacher reference, not student feedback):
"You are currently at Band 2. Band 3 requires deeper analytical engagement."

RIGHT:
"Your points explained what happened but stopped short of saying why it
mattered."

RIGHT:
"Your evidence was there but dropped in rather than woven into your argument."

The student should be able to act on the feedback without ever seeing the
rubric."""


# Shared rules for the per-question "correction_prompt" string. Replaces the
# old fixed boilerplate ("In your own words, explain what you should have
# written and why") with a typology-driven prompt that varies by mistake type
# so the student isn't asked the same generic question on every gap.
CORRECTION_PROMPT_RULES = """CORRECTION PROMPT RULES (for the "correction_prompt" field)

The correction prompt is a one-line task the student does on the spot — a
short do-this-now exercise, not a reflection question. Vary it to match the
mistake type. Pick exactly ONE form per criterion based on what went wrong:

- Procedural / careless slip (computation error, wrong unit, missed step):
  "Re-do the [specific step] using [the correct method or value]."

- Reasoning gap (link not made, cause→effect missing, comparison flat):
  "Explain the link between [X] and [Y]."

- Evidence / source handling (quotation missing, source not unpacked):
  "Pick one quote from [Source A] and explain what it suggests about [Z]."

- Content / concept gap (term defined wrongly, idea misunderstood):
  "In your own words, define [the concept] and explain how it applies here."

- Language / expression (clarity, register, sentence structure):
  "Rewrite the sentence so it [specific quality the student needs]."

CONSTRAINTS:
- ≤ 25 words. One imperative sentence.
- Reference the specific content of THIS criterion — never a generic stem.
- Across all correction_prompt fields in the SAME response, no two prompts
  may end with the same words. Vary the verb and the focus.
- Do NOT use "In your own words, explain what you should have written and
  why" — that's the old boilerplate and is banned.
- OMIT this field entirely on full-marks (or "correct") criteria."""


def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages,
                          script_pages, review_section, marking_section, total_marks,
                          calibration_block=''):
    """Build system prompt and content for rubrics/essay marking."""
    reference_section = ""
    if reference_pages:
        reference_section = "\nREFERENCE MATERIALS (sample works or other references) have been provided — use them to calibrate your expectations."

    system_prompt = f"""{calibration_block}You are an experienced teacher marking a student's essay/extended response using rubrics.

Subject: {subject or 'General'}
{reference_section}
{review_section}
{marking_section}

Your task:
1. Read the QUESTION PAPER to understand the essay prompt/task
2. Read the GRADING RUBRICS carefully — these are your PRIMARY evaluation criteria
3. Read the STUDENT SCRIPT thoroughly
4. If REFERENCE MATERIALS are provided, use them to calibrate expectations
5. Evaluate the essay against EACH rubric criterion and determine which band the student falls into
6. Identify specific line-by-line errors (grammar, spelling, punctuation, factual, logical)

CRITICAL — EXTRACTING CRITERIA FROM THE RUBRICS:
- The rubrics document contains one or more TABLES. Each table represents ONE criterion.
- You MUST identify each distinct criterion table (e.g. "Task Fulfilment", "Language", "Content", "Organisation").
- The number of criteria = the number of band descriptor tables in the rubrics. Do NOT invent extra criteria.
- For each criterion, read the Band and Marks columns to find the EXACT mark range (e.g. Band 5 = 17-20).
- "marks_total" for each criterion = the MAXIMUM marks shown in that criterion's table (the highest number in the Marks column).
- Award "marks_awarded" within the mark range of the band the student falls into.
- The sum of all criteria marks_total should equal the total across all rubric tables.

RUBRIC EVALUATION:
- For each criterion, determine which band the student's work falls into
- Quote the band descriptor that best matches the student's performance
- Assign a status: "correct" (top bands), "partially_correct" (middle bands), "incorrect" (lowest bands)

LINE-BY-LINE ERROR IDENTIFICATION:
- Find specific errors in the student's writing
- Include the original text, the correction, and the error type
- Error types: grammar, spelling, punctuation, vocabulary, factual, logical, style
- Quote the exact text from the essay

{FEEDBACK_GENERATION_RULES}

{RUBRIC_FEEDBACK_RULES}

{CORRECTION_PROMPT_RULES}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Use LaTeX in $ delimiters for math: $x^2 + 3x = 0$

TIERED FEEDBACK FOR THE STUDENT — mandatory.

- "well_done" (top level): one sentence naming ONE specific thing done well. No generic phrases like "good effort".
- "main_gap" (top level): one sentence (≤30 words) naming the single most important gap.
- For each criterion, the "feedback" field is diagnostic, MAX 2 sentences. First sentence: what was present/correct. Second sentence: what was missing/wrong and why marks were lost. Never use "well done", "good attempt", "you demonstrated", "it is important to note", "however", "overall".
- For any criterion where marks_awarded < marks_total, include "correction_prompt" — see CORRECTION PROMPT RULES above for the typology and constraints. Omit on full-marks criteria.

Respond ONLY with valid JSON:
{{
    "well_done": "one specific thing done correctly (one sentence)",
    "main_gap": "the single most important gap (one sentence, ≤30 words)",
    "questions": [
        {{
            "question_num": 1,
            "criterion_name": "the exact criterion name from the rubrics table heading",
            "band": "Band X (mark range)",
            "student_answer": "summary of what the student demonstrated for this criterion",
            "correct_answer": "the band descriptor text that best matches the student's level",
            "status": "correct | partially_correct | incorrect",
            "marks_awarded": number,
            "marks_total": number,
            "feedback": "single Feedback sentence — see FEEDBACK GENERATION RULES (≤20 words, diagnosis only)",
            "improvement": "single Suggested Improvement sentence — see FEEDBACK GENERATION RULES (≤20 words)",
            "correction_prompt": "OMIT if marks_awarded == marks_total; otherwise one short do-this-now task following CORRECTION PROMPT RULES — pick the form that matches this criterion's mistake type (procedural / reasoning / evidence / concept / language). ≤ 25 words. Must not duplicate another criterion's wording."
        }}
    ],
    "errors": [
        {{
            "location": "Paragraph X, Line Y or quote context",
            "original": "exact text with the error",
            "correction": "corrected version",
            "type": "grammar | spelling | punctuation | vocabulary | factual | logical | style"
        }}
    ],
    "overall_feedback": "holistic assessment of the essay with band placement summary",
    "recommended_actions": ["action 1", "action 2", "action 3"]
}}

IMPORTANT:
- The number of entries in "questions" MUST equal the number of criteria tables in the rubrics.
- Use the EXACT criterion name from the rubrics as "criterion_name" (e.g. "Task Fulfilment", "Language").
- Use the EXACT mark ranges from the rubrics — do NOT assume all criteria have the same max marks."""

    content = []
    _append_pages(content, "QUESTION PAPER / ESSAY PROMPT:", question_paper_pages)

    if reference_pages:
        _append_pages(content, "\nREFERENCE MATERIALS (sample works for calibration):", reference_pages)

    if rubrics_pages:
        _append_pages(content, "\nGRADING RUBRICS (use these as primary evaluation criteria):", rubrics_pages)

    # Cache breakpoint — everything above is identical across all students of
    # this assignment within the 5-minute cache window. Student script and
    # final instruction stay outside the cached prefix.
    _mark_anthropic_cache_breakpoint(content)

    _append_pages(content, "\nSTUDENT SCRIPT (evaluate this essay):", script_pages)

    content.append({"type": "text", "text": "\nEvaluate this essay against the rubrics and identify line-by-line errors. Provide JSON feedback:"})

    return system_prompt, content


def _build_short_answer_prompt(subject, rubrics_pages, answer_key_pages, question_paper_pages,
                               script_pages, review_section, marking_section, scoring_mode, total_marks,
                               calibration_block=''):
    """Build system prompt and content for short answer marking."""
    rubrics_section = ""
    if rubrics_pages:
        rubrics_section = "\nGRADING RUBRICS have been provided — use them to evaluate subjective answers."

    tiered_feedback_instructions = """

TIERED FEEDBACK FOR THE STUDENT — mandatory, not optional.

You MUST produce, in addition to the per-question fields below, a two-row "verdict" at the top of the JSON:

  "well_done": one sentence naming ONE specific thing the student got right. No generic phrases like "good effort" or "you showed understanding". Point at the actual answer.
  "main_gap": one sentence (≤ 30 words) naming the SINGLE most important gap, specific enough that the student knows exactly what to fix.

The per-question "feedback" and "improvement" fields follow the FEEDBACK GENERATION RULES below verbatim — same word limits, same distance gating, same banned wording. Do not contradict those rules here.

For any question where marks_awarded < marks_total, also include:
  "correction_prompt": a one-line do-this-now task following the CORRECTION
  PROMPT RULES section below. Pick the form that matches this question's
  mistake type (procedural / reasoning / evidence / concept / language).
  ≤ 25 words. No two correction_prompts in the response may end identically.
  Omit this field on questions that got full marks.

Do not include "The idea" / "Next time" explanations here — those are generated on demand.
"""

    if scoring_mode == 'marks':
        total_marks_str = total_marks or '100'
        scoring_instructions = f"""SCORING (numerical): Award marks for every question and sub-part.

The assignment's total is {total_marks_str} marks.

★ HOW TO FIND THE MARK ALLOCATION — DO THIS FIRST, BEFORE ANYTHING ELSE ★

Scan the QUESTION PAPER carefully for mark allocations. They are almost always next to or at the right of each question/sub-part, written in one of these forms:
  • Square brackets: [2], [3], [5], [10]
  • Parentheses near the end of the line: (2 marks), (3)
  • Curly braces: {{2}}
  • A number in the right margin aligned with the question

Sub-parts are lettered or numbered. If you see "1(a) ... [2]", "1(b) ... [3]", "1(c) ... [5]", that is THREE separate sub-parts with three separate totals. Emit THREE JSON entries with question_num "1a", "1b", "1c" and marks_total 2, 3, 5 respectively — NEVER merge them into a single entry for Q1.

Cross-check against the ANSWER KEY: the key typically shows how marks are broken down (e.g. "1 mark for method, 1 mark for answer"). Use the key to inform what earns each mark, but the BRACKETED NUMBER IN THE QUESTION PAPER is the authoritative total for that part.

★ NEVER LEAVE marks_total BLANK OR ZERO ★

Every question entry MUST have a positive integer marks_total. If a part has no bracketed number AND the answer key gives no clear allocation, fall back to distributing the remaining assignment total ({total_marks_str}) evenly across the parts that have no allocation. Say so in the feedback.

★ SELF-CHECK BEFORE YOU RESPOND ★

After drafting your JSON, add up every marks_total. The sum must equal {total_marks_str}. If it doesn't, re-read the question paper — you either missed a sub-part, merged sub-parts that should be separate, or misread a bracket.

marks_awarded must be a non-negative number ≤ marks_total for that part. status is derived: equal → correct, 0 → incorrect, in between → partially_correct.

Include marks_awarded, marks_total, and status on EVERY entry."""

        question_schema = """{{
            "question_num": 1,
            "student_answer": "transcribed answer from the script",
            "correct_answer": "answer from the answer key",
            "status": "correct | partially_correct | incorrect",
            "marks_awarded": number,
            "marks_total": number,
            "feedback": "single Feedback sentence — see FEEDBACK GENERATION RULES (≤20 words, diagnosis only)",
            "improvement": "single Suggested Improvement sentence — see FEEDBACK GENERATION RULES (≤20 words)",
            "correction_prompt": "OMIT if marks_awarded == marks_total; otherwise one short do-this-now task following CORRECTION PROMPT RULES — pick the form matching this question's mistake type (procedural / reasoning / evidence / concept / language). ≤ 25 words. Must not duplicate another question's wording."
        }}"""
    else:
        scoring_instructions = """SCORING: For each question, assign one of these statuses:
- "correct" — answer is accurate and complete
- "partially_correct" — answer shows understanding but is incomplete or has minor errors
- "incorrect" — answer is wrong or fundamentally flawed"""

        question_schema = """{{
            "question_num": 1,
            "student_answer": "transcribed answer from the script",
            "correct_answer": "answer from the answer key",
            "status": "correct | partially_correct | incorrect",
            "feedback": "single Feedback sentence — see FEEDBACK GENERATION RULES (≤20 words, diagnosis only)",
            "improvement": "single Suggested Improvement sentence — see FEEDBACK GENERATION RULES (≤20 words)",
            "correction_prompt": "OMIT if status == 'correct'; otherwise one short do-this-now task following CORRECTION PROMPT RULES — pick the form matching this question's mistake type (procedural / reasoning / evidence / concept / language). ≤ 25 words. Must not duplicate another question's wording."
        }}"""

    system_prompt = f"""{calibration_block}You are an experienced teacher marking a student's assignment script.

Subject: {subject or 'General'}
{rubrics_section}
{review_section}
{marking_section}

Your task:
1. Read the QUESTION PAPER to understand what was asked
2. Read the ANSWER KEY to know the correct answers
3. Read the STUDENT SCRIPT and evaluate each answer
4. If RUBRICS are provided, use them for evaluation criteria

{scoring_instructions}
{tiered_feedback_instructions}

{FEEDBACK_GENERATION_RULES}

{CORRECTION_PROMPT_RULES}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Use LaTeX in $ delimiters for math: $x^2 + 3x = 0$
- Use $$ for display equations: $$E = mc^2$$

Respond ONLY with valid JSON:
{{
    "well_done": "one specific thing done correctly (one sentence)",
    "main_gap": "the single most important gap (one sentence, ≤30 words)",
    "questions": [
        {question_schema}
    ],
    "overall_feedback": "general assessment of the submission",
    "recommended_actions": ["action 1", "action 2", "action 3"]
}}"""

    content = []
    _append_pages(content, "QUESTION PAPER:", question_paper_pages)
    _append_pages(content, "\nANSWER KEY (use for marking):", answer_key_pages)

    if rubrics_pages:
        _append_pages(content, "\nGRADING RUBRICS:", rubrics_pages)

    # Cache breakpoint — everything above is identical across all students of
    # this assignment within the 5-minute cache window. Student script and
    # final instruction stay outside the cached prefix.
    _mark_anthropic_cache_breakpoint(content)

    _append_pages(content, "\nSTUDENT SCRIPT (evaluate this):", script_pages)

    content.append({"type": "text", "text": "\nAnalyze this submission and provide JSON feedback:"})

    return system_prompt, content


def extract_answers(provider, question_paper_pages, script_pages,
                    subject='', assign_type='short_answer',
                    model=None, session_keys=None):
    """
    Extract student answers from a script using AI vision (no marking).

    Returns dict with 'answers' list: [{"question_num": 1, "extracted_text": "..."}]
    For rubrics mode, returns a single entry with the full essay text.
    """
    client, model_name, prov = get_ai_client(provider, model=model, session_keys=session_keys)
    if not client:
        return {'error': f'AI provider "{provider}" is not available (no API key configured)'}

    if assign_type == 'rubrics':
        system_prompt = f"""You are an experienced teacher's assistant. Your ONLY task is to accurately transcribe what the student has written.

Subject: {subject or 'General'}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections
- Preserve paragraph breaks and formatting

Respond ONLY with valid JSON:
{{
    "answers": [
        {{
            "question_num": 1,
            "label": "Essay Response",
            "extracted_text": "the full transcribed essay text, preserving paragraphs"
        }}
    ]
}}"""
    else:
        system_prompt = f"""You are an experienced teacher's assistant. Your ONLY task is to accurately transcribe what the student has written for each question. Do NOT mark or evaluate — just extract the text.

Subject: {subject or 'General'}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Use LaTeX in $ delimiters for math: $x^2 + 3x = 0$

Respond ONLY with valid JSON:
{{
    "answers": [
        {{
            "question_num": 1,
            "label": "Question 1",
            "extracted_text": "the student's transcribed answer"
        }}
    ]
}}

Extract ALL questions you can identify from the student script. Match question numbers to the question paper."""

    content = []
    if question_paper_pages:
        _append_pages(content, "QUESTION PAPER (use to identify question numbers):", question_paper_pages)
    _append_pages(content, "\nSTUDENT SCRIPT (transcribe the answers from this):", script_pages)
    content.append({"type": "text", "text": "\nExtract and transcribe ALL student answers. Return JSON only:"})

    try:
        response_text = make_ai_api_call(
            client=client,
            model_name=model_name,
            provider=prov,
            system_prompt=system_prompt,
            messages_content=content,
            max_tokens=16000
        )

        result = parse_ai_response(response_text)
        if 'error' in result and 'answers' not in result:
            return result
        return {'answers': result.get('answers', []), 'assign_type': assign_type}

    except Exception as e:
        logger.error(f"Error extracting answers with {provider}: {e}")
        err_str = str(e)
        is_413 = '413' in err_str or 'request_too_large' in err_str.lower()
        return {
            'error': (
                'Files too large for AI processing. Try smaller images or fewer pages.'
                if is_413 else f'Error from {provider}: {err_str}'
            )
        }


def mark_script(provider, question_paper_pages, answer_key_pages, script_pages,
                subject='', rubrics_pages=None, reference_pages=None,
                review_instructions='', marking_instructions='',
                model=None, assign_type='short_answer', scoring_mode='status', total_marks='',
                session_keys=None, calibration_block=''):
    """
    Mark a student script using AI vision.

    Args:
        question_paper_pages: List of file bytes (each is a PDF or image)
        answer_key_pages: List of file bytes (used for short_answer mode)
        script_pages: List of file bytes
        rubrics_pages: Optional list of file bytes
        reference_pages: Optional list of file bytes (sample works / reference for rubrics mode)
        assign_type: 'short_answer' or 'rubrics'
        scoring_mode: 'status' (correct/partial/incorrect) or 'marks' (numerical)
        total_marks: Total marks for the assignment (when scoring_mode is 'marks')

    Returns dict with questions, overall_feedback, recommended_actions.
    For rubrics mode, also returns errors (line-by-line) and assign_type.
    """
    client, model_name, prov = get_ai_client(provider, model=model, session_keys=session_keys)
    if not client:
        return {'error': f'AI provider "{provider}" is not available (no API key configured)'}

    review_section = ""
    if review_instructions.strip():
        review_section = f"\n\nREVIEW INSTRUCTIONS (follow these for how to write feedback):\n{review_instructions.strip()}"

    marking_section = ""
    if marking_instructions.strip():
        marking_section = f"\n\nMARKING INSTRUCTIONS (follow these for how to evaluate answers):\n{marking_instructions.strip()}"

    if assign_type == 'rubrics':
        system_prompt, content = _build_rubrics_prompt(
            subject, rubrics_pages, reference_pages, question_paper_pages, script_pages,
            review_section, marking_section, total_marks,
            calibration_block=calibration_block,
        )
    else:
        system_prompt, content = _build_short_answer_prompt(
            subject, rubrics_pages, answer_key_pages, question_paper_pages, script_pages,
            review_section, marking_section, scoring_mode, total_marks,
            calibration_block=calibration_block,
        )

    try:
        response_text = make_ai_api_call(
            client=client,
            model_name=model_name,
            provider=prov,
            system_prompt=system_prompt,
            messages_content=content,
            max_tokens=32000
        )

        result = parse_ai_response(response_text)
        result['assign_type'] = assign_type
        result['generated_at'] = datetime.now(timezone.utc).isoformat()
        result['provider'] = provider
        result['model'] = model_name
        prov_config = PROVIDERS.get(provider, {})
        model_label = prov_config.get('models', {}).get(model_name, model_name)
        result['provider_label'] = f"{prov_config.get('label', provider)} — {model_label}"
        return result

    except Exception as e:
        logger.error(f"Error marking script with {provider}: {e}")
        err_str = str(e)
        is_413 = '413' in err_str or 'request_too_large' in err_str.lower()
        return {
            'error': (
                'Files too large for AI processing. Try smaller images or fewer pages.'
                if is_413 else f'Error from {provider}: {err_str}'
            )
        }


def generate_exemplar_analysis(provider, model, session_keys, subject, submissions_data):
    """Run AI exemplar analysis across all done submissions for a class.

    submissions_data: list of dicts, one per done submission, each with:
        - submission_id (int)
        - student_name (str)
        - marks_awarded (int or None)
        - marks_total (int or None)
        - questions: list of {question_num, student_answer, correct_answer, feedback, improvement, status}
        - overall_feedback (str)
        - page_count (int)

    Returns: {"areas": [{question_part, label, description,
              needs_work_examples:[{submission_id, page_index, note}],
              strong_examples:[{submission_id, page_index, note}]}, ...]}

    Raises an Exception on API/parse failure; the caller translates to HTTP.
    """
    import json as _json

    # Build a compact textual representation to fit the prompt.
    lines = [f"Subject: {subject}", f"Total students: {len(submissions_data)}", ""]
    for s in submissions_data:
        score = ''
        if s.get('marks_awarded') is not None and s.get('marks_total') is not None:
            score = f" ({s['marks_awarded']}/{s['marks_total']})"
        lines.append(f"--- Student (submission_id={s['submission_id']}){score} | pages={s['page_count']} ---")
        for q in s.get('questions') or []:
            qn = q.get('question_num') or '?'
            ans = (q.get('student_answer') or '').strip().replace('\n', ' ')
            fb = (q.get('feedback') or '').strip().replace('\n', ' ')
            if len(ans) > 400:
                ans = ans[:400] + '…'
            if len(fb) > 300:
                fb = fb[:300] + '…'
            lines.append(f"Q{qn}: student_answer: {ans}")
            if fb:
                lines.append(f"Q{qn}: feedback: {fb}")
        if s.get('overall_feedback'):
            of = s['overall_feedback'].strip().replace('\n', ' ')
            if len(of) > 300:
                of = of[:300] + '…'
            lines.append(f"overall_feedback: {of}")
        lines.append("")
    user_prompt = "\n".join(lines)

    system_prompt = (
        "You are an education analytics assistant preparing exemplars for a post-marking class discussion.\n\n"
        "You will receive every student's answers and AI feedback for a class's assignment. Produce a short JSON list of 'areas for analysis'. "
        "Each area should be:\n"
        "- Tied to a SPECIFIC, CONCRETE issue observed in the ACTUAL submissions — not a generic textbook category. "
        "Label it so a teacher scanning a grid of buttons can tell what the area is about at a glance. "
        "Example good labels: 'Used weight instead of mass in F=ma', 'Missed the word \"except\" in Q3', 'Weak topic sentence in intro'. "
        "Example bad labels (too generic): 'Misconception about force', 'Question-answering technique', 'Paragraph structure'.\n"
        "- Cross-subject: include question-answering technique issues (misread the question, missed keywords, "
        "didn't quote evidence, answered a different question, ignored mark allocation) alongside conceptual misconceptions, "
        "procedural errors, presentation issues, and argumentation issues, as appropriate to the subject.\n"
        "- Accompanied by FOUR concrete exemplars: TWO students whose work illustrates the issue (needs_work_examples) "
        "and TWO whose work handles it well (strong_examples). For each exemplar give submission_id (integer, must match one of the "
        "students above), page_index (0-based integer, must be < that student's page_count), and a short note pointing to where on the page to look.\n\n"
        "Return 3–8 areas, ordered by teaching value (most discussion-worthy first). Exemplars within one area should be four DIFFERENT students.\n\n"
        "Respond ONLY with valid JSON in this exact shape:\n"
        '{"areas":[{"question_part":"...","label":"...","description":"...",'
        '"needs_work_examples":[{"submission_id":0,"page_index":0,"note":"..."},{"submission_id":0,"page_index":0,"note":"..."}],'
        '"strong_examples":[{"submission_id":0,"page_index":0,"note":"..."},{"submission_id":0,"page_index":0,"note":"..."}]'
        '}]}'
    )

    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    api_key = _resolve_api_key(provider, session_keys)
    if not api_key:
        raise ValueError(f"No API key configured for provider: {provider}")

    if provider == 'anthropic':
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        text = resp.content[0].text
    else:
        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK not installed")
        if provider == 'qwen':
            client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        else:
            client = OpenAI(api_key=api_key)
        kwargs = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        if provider == 'openai':
            kwargs['max_completion_tokens'] = 4096
        else:
            kwargs['max_tokens'] = 4096
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError("AI response contained no JSON object")
    parsed = _json.loads(match.group())
    if 'areas' not in parsed or not isinstance(parsed['areas'], list):
        raise ValueError("AI response missing 'areas' list")
    return parsed


# ---------------------------------------------------------------------------
# Tiered feedback helpers (student-facing)
# ---------------------------------------------------------------------------

def _run_text_completion(provider, model, session_keys, system_prompt, user_prompt, max_tokens=400):
    """Run a single chat completion and return the RAW response text (no JSON
    parsing). Use this when the response is mixed reasoning + a tagged JSON
    block (the "Group by Mistake Type" Pass 1 prompt does this)."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    api_key = _resolve_api_key(provider, session_keys)
    if not api_key:
        raise ValueError(f"No API key configured for provider: {provider}")

    if provider == 'anthropic':
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        return resp.content[0].text
    if not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI SDK not installed")
    if provider == 'qwen':
        client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    else:
        client = OpenAI(api_key=api_key)
    kwargs = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }
    if provider == 'openai':
        kwargs['max_completion_tokens'] = max_tokens
    else:
        kwargs['max_tokens'] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def _run_feedback_helper(provider, model, session_keys, system_prompt, user_prompt, max_tokens=400):
    """Shared single-shot JSON-returning call used by Layer 3 explain and correction evaluation."""
    import json as _json
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    api_key = _resolve_api_key(provider, session_keys)
    if not api_key:
        raise ValueError(f"No API key configured for provider: {provider}")

    if provider == 'anthropic':
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        text = resp.content[0].text
    else:
        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK not installed")
        if provider == 'qwen':
            client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        else:
            client = OpenAI(api_key=api_key)
        kwargs = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        if provider == 'openai':
            kwargs['max_completion_tokens'] = max_tokens
        else:
            kwargs['max_tokens'] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError("AI response contained no JSON object")
    return _json.loads(match.group())


def explain_criterion(provider, model, session_keys, subject, criterion_name,
                      student_answer, expected_answer, feedback_sentence=''):
    """Generate Layer 3 'The idea' for one criterion.

    Returns: {"idea": str}

    Layer 3's "Next time" line is populated client-side from the criterion's
    existing `improvement` field (the same Suggested Improvement shown on the
    PDF). No separate AI call — keeps the two surfaces in lockstep.
    """
    system_prompt = (
        "You help a student understand WHY this criterion matters.\n\n"
        "Return JSON with exactly one field — and nothing else:\n"
        '  "idea": ONE sentence. Anchor it in what the question is actually asking for '
        'or in the underlying concept being applied — how knowledge is being used here. '
        'Do NOT frame it around examiners, markers, markschemes or what "examiners want". '
        'Do NOT start with "Examiners want", "Markers look for", "This criterion tests", '
        'or similar meta phrasing. Do NOT restate the criterion name. Speak as if explaining '
        "to the student directly why this part of the answer matters for the question.\n\n"
        "≤ 25 words. No fluff, no encouragement phrases, no repetition of the criterion name."
    )
    user_prompt = (
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Student's answer (excerpt): {(student_answer or '')[:600]}\n"
        f"Expected answer / band descriptor (excerpt): {(expected_answer or '')[:600]}\n"
        f"Teacher's feedback on this criterion: {(feedback_sentence or '')[:400]}\n\n"
        "Return the JSON now."
    )
    helper_model = _helper_model_for(provider, model)
    parsed = _run_feedback_helper(provider, helper_model, session_keys, system_prompt, user_prompt, max_tokens=200)
    idea = (parsed.get('idea') or '').strip()
    return {'idea': idea}


def evaluate_correction(provider, model, session_keys, subject, criterion_name,
                        expected_answer, feedback_sentence, attempt_text):
    """Evaluate a student's "Now You Try" correction attempt.

    Returns: {"verdict": "good" | "not_quite", "message": "≤20-word comment"}
    """
    system_prompt = (
        "You are evaluating a student's second attempt at answering a question they got wrong.\n\n"
        "Return JSON with exactly two fields and nothing else:\n"
        '  "verdict": "good" if the attempt captures the key idea they previously missed, '
        'otherwise "not_quite".\n'
        '  "message": A single sentence, ≤ 20 words.\n'
        '    - If verdict is "good": start with "Good — that\'s the right idea." then one '
        "sentence on what they got right.\n"
        '    - If verdict is "not_quite": start with "Not quite — " then a one-sentence '
        "redirect.\n\n"
        "Be generous about phrasing; judge the underlying idea, not the wording. Never use "
        '"however", "overall", or filler praise.'
    )
    user_prompt = (
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Expected answer / key point (excerpt): {(expected_answer or '')[:600]}\n"
        f"Original teacher feedback on the student's first attempt: {(feedback_sentence or '')[:400]}\n"
        f"Student's new attempt: {(attempt_text or '')[:800]}\n\n"
        "Return the JSON now."
    )
    helper_model = _helper_model_for(provider, model)
    parsed = _run_feedback_helper(provider, helper_model, session_keys, system_prompt, user_prompt, max_tokens=200)
    verdict = (parsed.get('verdict') or '').strip().lower()
    message = (parsed.get('message') or '').strip()
    if verdict not in ('good', 'not_quite'):
        verdict = 'not_quite'
    # Enforce the expected openings.
    if verdict == 'good' and not message.lower().startswith('good'):
        message = "Good — that's the right idea. " + message
    if verdict == 'not_quite' and not message.lower().startswith('not quite'):
        message = 'Not quite — ' + message
    return {'verdict': verdict, 'message': message}


# ---------------------------------------------------------------------------
# Subject family classification (run ONCE at assignment creation) +
# mistake categorisation (run ONCE per submission after marking, async).
# ---------------------------------------------------------------------------

SUBJECT_FAMILIES = [
    'science',
    'humanities_seq',       # essay-type humanities (rubric)
    'humanities_sbq',       # source-based questions humanities (answer key)
    'literature',
    'mother_tongue_comprehension',
    'mother_tongue_composition',
    'mother_tongue_translation',
]


def classify_subject_family(provider, model, session_keys, subject, assign_type,
                             has_rubric=False, has_answer_key=False):
    """One-shot classification of a freeform subject string into one of
    SUBJECT_FAMILIES. Uses the marking format as a strong signal:

        rubric     → essay-type          → *_seq / composition / literature
        answer key → short-answer / SBQ  → *_sbq / comprehension / science

    Returns a key string. Falls back to the closest family on any error or
    low-confidence response so the caller never has to handle None.
    """
    # A couple of cheap shortcuts — save an API call when the subject is
    # obvious. Anything ambiguous still goes to the AI.
    s = (subject or '').strip().lower()
    if s:
        if any(w in s for w in ['biology', 'chemistry', 'physics', 'science', 'combined science', 'bio', 'chem', 'phy']):
            return 'science'
        if 'literature' in s or 'lit ' in s or s.endswith(' lit'):
            return 'literature'

    format_hint = 'rubric (essay)' if has_rubric else ('answer key' if has_answer_key else assign_type or 'unknown')

    system_prompt = (
        "You classify a subject string for a Singapore secondary school assignment "
        "into exactly one of these family keys:\n\n"
        "  science                      - any science subject (biology, chemistry, physics, combined)\n"
        "  humanities_seq               - humanities essay-type question (marked by rubric). "
        "Subjects: history, geography, social studies, economics — when the marking format is a rubric.\n"
        "  humanities_sbq               - humanities source-based question (marked against an answer key). "
        "Same subjects as above but when the marking format is an answer key / mark scheme.\n"
        "  literature                   - English Literature or any literature-in-a-language course\n"
        "  mother_tongue_comprehension  - Chinese / Malay / Tamil comprehension papers\n"
        "  mother_tongue_composition    - Chinese / Malay / Tamil composition / essay (rubric)\n"
        "  mother_tongue_translation    - Chinese / Malay / Tamil translation exercises\n\n"
        "Marking format is an important disambiguator: rubric implies essay-type; answer key "
        "implies SBQ or comprehension.\n\n"
        "Return JSON ONLY in this shape: {\"family\": \"<one key>\"}. If genuinely ambiguous, "
        "choose the most semantically similar family — never leave it blank."
    )
    user_prompt = (
        f"Subject string: {subject!r}\n"
        f"Marking format: {format_hint}\n"
        f"Assignment type flag: {assign_type or 'unknown'}\n\n"
        "Return the JSON now."
    )

    try:
        parsed = _run_feedback_helper(provider, model, session_keys, system_prompt, user_prompt, max_tokens=60)
        key = (parsed.get('family') or '').strip().lower()
        if key in SUBJECT_FAMILIES:
            return key
    except Exception as e:
        logger.warning(f"classify_subject_family failed for {subject!r}: {e}")

    # Heuristic fallback when the AI fails or returns an unknown key.
    if has_rubric:
        return 'humanities_seq'
    return 'humanities_sbq'


def _extract_step1_diagnoses(text, criteria):
    """Pull per-criterion Step 1 diagnoses out of a Pass 1 reasoning trace.

    Returns a dict mapping criterion_id → diagnosis sentence (best effort).
    Used to feed Pass 2's verifier and Pass 3's habit generator with the
    underlying error analysis without re-asking the model.
    """
    import json as _json  # noqa: F401
    diag_by_id = {}
    if not text:
        return diag_by_id

    # Take the slice between STEP 1 and STEP 2 (or end of text if Step 2 is missing).
    m = re.search(r'STEP\s*1[^\n]*\n([\s\S]*?)(?:STEP\s*2|FINAL_JSON|\Z)', text, re.IGNORECASE)
    section = m.group(1) if m else text

    pairs = re.findall(
        r'Criterion\s*[:\-]\s*([^\n]+)\s*\n\s*Diagnosis\s*[:\-]\s*([^\n]+)',
        section,
        re.IGNORECASE,
    )
    if not pairs:
        return diag_by_id

    by_name = {}
    by_id = {}
    for c in criteria:
        cid = str(c.get('criterion_id') or '')
        cname = (c.get('criterion_name') or '').strip().lower()
        if cid:
            by_id[cid.lower()] = cid
        if cname:
            by_name[cname] = cid

    for raw_crit, raw_diag in pairs:
        crit = raw_crit.strip().lower().rstrip('.,;:')
        diag = raw_diag.strip()
        cid = by_name.get(crit)
        if not cid:
            cid = by_id.get(crit)
        if not cid:
            # Fallback: substring match (e.g. "Q2 — Use of evidence" against criterion_name "Use of evidence").
            for cname, mapped_cid in by_name.items():
                if cname and cname in crit:
                    cid = mapped_cid
                    break
        if cid:
            diag_by_id[cid] = diag

    return diag_by_id


def _extract_final_json(text, marker='FINAL_JSON'):
    """Find the JSON object that follows a labelled marker like FINAL_JSON:.

    The Pass 1 prompt asks the model to write reasoning before its JSON, so we
    cannot just regex the first ``{...}`` (the model often shows examples
    earlier in its trace). The marker disambiguates.
    """
    import json as _json
    m = re.search(rf'{re.escape(marker)}\s*[:\-]?\s*(\{{[\s\S]*\}})\s*$', text)
    if not m:
        m = re.search(rf'{re.escape(marker)}\s*[:\-]?\s*(\{{[\s\S]*?\}})', text)
    if not m:
        # Last resort: grab the LAST complete-looking JSON object in the text.
        objs = re.findall(r'(\{[\s\S]*?\})', text)
        if not objs:
            raise ValueError(f"No JSON object found after {marker}")
        return _json.loads(objs[-1])
    return _json.loads(m.group(1))


def categorise_mistakes(provider, model, session_keys, subject_family, themes, questions_data):
    """Three-pass categorisation pipeline:

      Pass 1 — generation. Asks the model to reason in four steps (diagnose →
        find shared causes → label → assign) and emit a FINAL_JSON block.
      Pass 2 — verification. Reviews each specific_label and rewrites any
        that are advice rather than diagnoses, or that are too generic.
        Falls back to Pass 1 on parse failure.
      Pass 3 — group habits. Generates one "Next time:" habit per group
        that survived Pass 2.

    `themes` is the THEMES dict from config/mistake_themes.py — passed in so
    this module never hardcodes theme data. `questions_data` is a list of
    dicts with criterion_id, criterion_name, student_answer, feedback,
    marks_awarded, marks_total (or marks_lost).

    Returns: {
        "categorisation": [ {criterion_id, theme_key, specific_label,
                             low_confidence, themed_correction_prompt: ""}, ... ],
        "group_habits":    [ {theme_key, habit}, ... ]
    }

    Raises on Pass 1 failure (caller marks the submission "failed"). Pass 2
    failure is logged and degraded; Pass 3 failure is logged and yields no
    habits.
    """
    import json as _json
    if not questions_data:
        return {'categorisation': [], 'group_habits': []}

    valid_keys = list(themes.keys())
    valid_keys_set = set(valid_keys)

    # ---- Render shared blocks for the prompts ----
    theme_lines = []
    for key, cfg in themes.items():
        theme_lines.append(
            f"  {key}: {cfg.get('label', key)} — {cfg.get('description', '')}"
            + ("  (never_group)" if cfg.get('never_group') else '')
        )
    theme_block = '\n'.join(theme_lines)
    theme_keys_csv = ', '.join(valid_keys)

    crit_lines = []
    for q in questions_data:
        cid = str(q.get('criterion_id') or '')
        cname = q.get('criterion_name') or cid
        ans = (q.get('student_answer') or '').strip().replace('\n', ' ')
        fb = (q.get('feedback') or '').strip().replace('\n', ' ')
        if len(ans) > 400:
            ans = ans[:400] + '…'
        if len(fb) > 400:
            fb = fb[:400] + '…'
        marks_lost = q.get('marks_lost')
        if marks_lost is None:
            ma = q.get('marks_awarded') or 0
            mt = q.get('marks_total') or 0
            marks_lost = max(0, (mt - ma)) if mt else 0
        crit_lines.append(
            f"- criterion_id: {cid}\n"
            f"  criterion_name: {cname}\n"
            f"  marks_lost: {marks_lost}\n"
            f"  student_answer: {ans}\n"
            f"  feedback: {fb}"
        )
    crits_block = '\n'.join(crit_lines)

    subject_family_str = subject_family or 'unknown'

    # ---------------------------------------------------------------
    # PASS 1 — generation with explicit four-step reasoning
    # ---------------------------------------------------------------
    pass1_system = f"""You are analysing a student's mistakes across an assignment to identify patterns. You will work through this in four explicit steps. Show your reasoning for each step before moving to the next.

Subject family: {subject_family_str}

Allowed parent themes (use EXACTLY one of these keys — no others):
{theme_block}

Allowed theme_keys: {theme_keys_csv}

STEP 1 — DIAGNOSE EACH MISTAKE
For each criterion where the student lost marks, write one sentence describing what the student actually did wrong. Write from a diagnostic perspective — describe the error, not what the student should do differently.

Format (use exactly this format, one block per criterion):
Criterion: <criterion_name OR criterion_id>
Diagnosis: <one sentence describing the error>

STEP 2 — FIND SHARED CAUSES
Look across all your Step 1 diagnoses. Group criteria that share the same underlying CAUSE — not the same topic or subject area, but the same type of error at its root.

A shared cause means: if a student fixed this one thing about how they think or work, it would address all the mistakes in this group.

List each candidate group and the criteria that belong to it. A group must have at least 2 criteria. Criteria that do not share a cause with any other should be marked as standalone.

STEP 3 — LABEL EACH GROUP
For each group from Step 2, generate a specific_label — a 2 to 4 word phrase that names the error TYPE as a diagnosis.

A diagnostic label names what went wrong. It does NOT tell the student what to do.

WRONG (these are advice, not diagnoses):
  "check conversions before moving on"
  "read the question more carefully"
  "show working clearly"
  "always state units"

RIGHT (these are diagnoses):
  "unit conversion error"
  "question requirement misread"
  "working not shown"
  "units omitted"

The label must be specific enough that two different error types would get two different labels. Generic labels that could apply to any subject or any mistake are not acceptable.

Also assign each group to exactly ONE theme_key from the five provided themes. If you are uncertain between two themes, choose the one that is more actionable for the student.

STEP 4 — ASSIGN AND OUTPUT
Assign each criterion to exactly one group, or mark it standalone. Then produce your final JSON output.

Return this EXACT structure and nothing else after your reasoning. Tag the JSON with the literal token FINAL_JSON: on its own line so it can be located unambiguously.

FINAL_JSON:
{{
  "categorisation": [
    {{
      "criterion_id": "<copied>",
      "theme_key": "<one of {theme_keys_csv}>",
      "specific_label": "<2-4 words, diagnostic>",
      "low_confidence": false
    }}
  ],
  "groups": [
    {{
      "theme_key": "<one of {theme_keys_csv}>",
      "specific_label": "<2-4 words, diagnostic>",
      "criteria_ids": ["<criterion_id>", "<criterion_id>"]
    }}
  ]
}}

Rules for the JSON:
- Every criterion with marks lost must appear in categorisation exactly once.
- low_confidence: true ONLY if you were genuinely uncertain between two themes after Step 3.
- A group appears in groups ONLY if it has 2 or more criteria.
- Standalone criteria still appear in categorisation with their theme_key and specific_label, but do not appear in groups.
- Never leave a criterion unassigned."""

    pass1_user = f"Criteria with marks lost:\n{crits_block}\n\nWork through Steps 1–4 then return FINAL_JSON."

    pass1_text = _run_text_completion(provider, model, session_keys, pass1_system, pass1_user, max_tokens=3500)

    try:
        pass1_json = _extract_final_json(pass1_text)
    except Exception as e:
        raise ValueError(f"Pass 1 FINAL_JSON could not be parsed: {e}")

    diagnoses = _extract_step1_diagnoses(pass1_text, questions_data)

    # ---------------------------------------------------------------
    # PASS 2 — verification (rewrite advice → diagnosis, fix generic labels)
    # ---------------------------------------------------------------
    pass2_input = _json.dumps({
        'categorisation': pass1_json.get('categorisation') or [],
        'groups': pass1_json.get('groups') or [],
    }, ensure_ascii=False)

    diag_block_lines = []
    for cid, diag in diagnoses.items():
        diag_block_lines.append(f"- {cid}: {diag}")
    diag_block = '\n'.join(diag_block_lines) if diag_block_lines else '(no diagnoses extracted)'

    pass2_system = """You are reviewing category labels generated for a student's assignment mistakes. Your job is to flag and fix any labels that are advice rather than diagnoses.

A diagnostic label names what went wrong.
An advice label tells the student what to do.

For each specific_label in the provided JSON, answer:
1. Is this label a diagnosis or advice?
2. If advice — rewrite it as a diagnosis in 2 to 4 words.
3. Is this label specific enough? A label is too generic if it could apply to any mistake in any subject. If too generic — rewrite it to be more specific based on the diagnosis provided.

Common advice patterns to catch and fix:
- Starts with a verb: "check...", "show...", "remember...", "always...", "read..."
- Contains "before", "next time", "make sure"
- Could appear on a generic study skills poster

After reviewing all labels, return the corrected JSON in the SAME structure as the input. Only change specific_label values — do not change any other fields. If a label is already correct, return it unchanged.

Return ONLY the corrected JSON. No explanation. No prose."""

    pass2_user = (
        "Criterion diagnoses (from Step 1):\n"
        f"{diag_block}\n\n"
        "Labels to verify (Pass 1 output):\n"
        f"{pass2_input}\n\n"
        "Return the corrected JSON now."
    )

    verified = pass1_json
    try:
        pass2_text = _run_text_completion(provider, model, session_keys, pass2_system, pass2_user, max_tokens=2500)
        m = re.search(r'\{[\s\S]*\}', pass2_text)
        if not m:
            raise ValueError("Pass 2 returned no JSON object")
        candidate = _json.loads(m.group(0))
        # Sanity: must have categorisation + groups keys.
        if isinstance(candidate, dict) and 'categorisation' in candidate and 'groups' in candidate:
            verified = candidate
        else:
            logger.warning("Pass 2 JSON missing expected keys; falling back to Pass 1")
    except Exception as e:
        logger.warning(f"Pass 2 parse failed, falling back to Pass 1: {e}")

    # ---------------------------------------------------------------
    # PASS 3 — group habits (one "Next time:" sentence per group)
    # ---------------------------------------------------------------
    verified_groups = verified.get('groups') or []
    # Drop never_group themes from habit generation (they should never bundle).
    eligible_groups = []
    for g in verified_groups:
        tk = g.get('theme_key')
        if tk not in valid_keys_set:
            continue
        if (themes.get(tk) or {}).get('never_group'):
            continue
        cids = [str(c) for c in (g.get('criteria_ids') or [])]
        if len(cids) < 2:
            continue
        eligible_groups.append({
            'theme_key': tk,
            'specific_label': (g.get('specific_label') or '').strip(),
            'criteria_ids': cids,
            'criterion_diagnoses': [diagnoses.get(cid, '') for cid in cids if diagnoses.get(cid)],
        })

    habits_out = []
    if eligible_groups:
        pass3_system = """For each group provided, generate a single "Next time:" sentence — one actionable habit the student can apply independently in future.

Rules:
- Must start with "Next time:"
- Maximum 20 words total INCLUDING "Next time:"
- Must name a concrete self-check action, not a vague reminder
- Must be specific to this group's error pattern, not generic study advice

WRONG: "Next time: check your work before submitting."
WRONG: "Next time: be more careful with your answers."
RIGHT: "Next time: after writing any quantity, ask yourself — have I stated the unit?"
RIGHT: "Next time: after describing a process, ask — what breaks if this step fails?"

Return JSON ONLY (a list — no surrounding object, no prose):
[
  {"theme_key": "...", "specific_label": "...", "habit": "Next time: ..."}
]"""

        pass3_user = (
            "Groups (each with the diagnoses from Step 1 of Pass 1):\n"
            f"{_json.dumps(eligible_groups, ensure_ascii=False)}\n\n"
            "Return the JSON list now."
        )
        try:
            pass3_text = _run_text_completion(provider, model, session_keys, pass3_system, pass3_user, max_tokens=600)
            m = re.search(r'\[[\s\S]*\]', pass3_text)
            if m:
                habs_in = _json.loads(m.group(0))
                seen_theme = set()
                for h in habs_in or []:
                    if not isinstance(h, dict):
                        continue
                    tk = h.get('theme_key')
                    if tk not in valid_keys_set or tk in seen_theme:
                        continue
                    if (themes.get(tk) or {}).get('never_group'):
                        continue
                    habit = (h.get('habit') or '').strip()
                    if not habit:
                        continue
                    if not habit.lower().startswith('next time'):
                        habit = 'Next time: ' + habit
                    seen_theme.add(tk)
                    habits_out.append({'theme_key': tk, 'habit': habit})
        except Exception as e:
            logger.warning(f"Pass 3 (group habits) failed: {e}")

    # ---------------------------------------------------------------
    # Sanitise the verified categorisation into the existing storage shape
    # ---------------------------------------------------------------
    cats_in = verified.get('categorisation') or []
    cats_out = []
    known_ids = {str(q.get('criterion_id')) for q in questions_data}
    seen_ids = set()
    for c in cats_in:
        if not isinstance(c, dict):
            continue
        cid = str(c.get('criterion_id') or '')
        if not cid or cid not in known_ids or cid in seen_ids:
            continue
        seen_ids.add(cid)
        tk = c.get('theme_key')
        if tk not in valid_keys_set:
            tk = 'content_gap'
        spec = (c.get('specific_label') or '').strip()
        if not spec:
            spec = (themes.get(tk) or {}).get('label', 'Area for review')
        cats_out.append({
            'criterion_id': cid,
            'theme_key': tk,
            'specific_label': spec,
            'low_confidence': bool(c.get('low_confidence')),
            # No themed correction prompt under the new pipeline; stays empty so
            # the existing UI swap is a no-op (generic prompt is shown instead).
            'themed_correction_prompt': '',
        })

    return {'categorisation': cats_out, 'group_habits': habits_out}


def _rubric_version_hash(asn):
    """MD5 hex over the assignment's raw rubric or answer_key bytes.

    rubrics and answer_key are LargeBinary blobs (uploaded files), not
    text. Hash the raw bytes — the spec's `.encode()` formulation
    doesn't apply to the actual columns. Empty/missing blobs hash the
    empty bytes string consistently, which is fine: such an
    assignment will only ever match other empty-blob assignments.
    """
    blob = (getattr(asn, 'rubrics', None) or getattr(asn, 'answer_key', None) or b'')
    if isinstance(blob, str):  # defensive — should be bytes from LargeBinary, but stay safe
        blob = blob.encode('utf-8')
    return hashlib.md5(blob).hexdigest()


def fetch_calibration_examples(teacher_id, assignment, theme_keys, limit=10):
    """Return up to `limit` of this teacher's prior active edits relevant to
    the current marking. Two tiers, merged then deduped:

      Tier 0: same assignment + same rubric_version (no theme filter).
      Tier 1: per theme_key — different assignment, same subject_family,
              theme_key matches.

    `theme_keys` is the iterable of theme_keys from the current submission's
    lost-mark criteria. May be empty (first mark of a fresh submission, or
    submission not yet categorised) — only Tier 0 returns rows in that case.

    All queries use bound parameters via SQLAlchemy text(). Never f-string
    interpolation.
    """
    from sqlalchemy import text as _sql_text
    from db import db
    if not teacher_id or not assignment:
        return []

    rubric_hash = _rubric_version_hash(assignment)
    rows_by_id = {}

    tier0_sql = _sql_text(
        "SELECT id, original_text, edited_text, theme_key, assignment_id, "
        "rubric_version, created_at, criterion_id, field, "
        "0 AS match_tier "
        "FROM feedback_edit "
        "WHERE edited_by = :teacher_id "
        "  AND active = true "
        "  AND assignment_id = :aid "
        "  AND rubric_version = :rubric_hash "
        "ORDER BY created_at DESC"
    )
    for r in db.session.execute(tier0_sql, {
        'teacher_id': teacher_id,
        'aid': assignment.id,
        'rubric_hash': rubric_hash,
    }).mappings().all():
        rows_by_id[r['id']] = dict(r)

    sf = getattr(assignment, 'subject_family', None) or ''
    if sf and theme_keys:
        tier1_sql = _sql_text(
            "SELECT id, original_text, edited_text, theme_key, assignment_id, "
            "rubric_version, created_at, criterion_id, field, "
            "1 AS match_tier "
            "FROM feedback_edit "
            "WHERE edited_by = :teacher_id "
            "  AND active = true "
            "  AND assignment_id != :aid "
            "  AND subject_family = :sf "
            "  AND theme_key IS NOT NULL "
            "  AND theme_key = :tk "
            "ORDER BY created_at DESC"
        )
        seen_themes = set()
        for tk in theme_keys:
            if not tk or tk in seen_themes:
                continue
            seen_themes.add(tk)
            for r in db.session.execute(tier1_sql, {
                'teacher_id': teacher_id,
                'aid': assignment.id,
                'sf': sf,
                'tk': tk,
            }).mappings().all():
                # Tier 0 wins over Tier 1 for the same edit id.
                if r['id'] not in rows_by_id:
                    rows_by_id[r['id']] = dict(r)

    # Sort: Tier 0 first, newest first within each tier.
    def _ts(d):
        ca = d.get('created_at')
        if ca is None:
            return 0
        try:
            return ca.timestamp()
        except Exception:
            return 0

    sorted_rows = sorted(
        rows_by_id.values(),
        key=lambda d: (d['match_tier'], -_ts(d)),
    )

    # Collapse to most-recent per (criterion_id, field), then truncate.
    seen_keys = set()
    out = []
    for d in sorted_rows:
        key = (d['criterion_id'], d['field'])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(d)
        if len(out) >= limit:
            break
    return out


def _truncate_at_word(s, max_chars=200):
    """Truncate to <= max_chars at the nearest word boundary, append '...'."""
    if not s:
        return ''
    s = s.replace('\n', ' ').replace('\r', ' ').strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    last_space = cut.rfind(' ')
    if last_space > max_chars * 0.5:
        cut = cut[:last_space]
    return cut.rstrip(' .,;:!?') + '...'


def format_calibration_block(examples):
    """Render `examples` (output of fetch_calibration_examples) as the
    MARKING CALIBRATION block per spec Part 3. Returns '' when empty so the
    caller can simply prepend without checking.
    """
    if not examples:
        return ''
    lines = [
        '---',
        'MARKING CALIBRATION',
        '',
        'This teacher has previously edited AI-generated feedback on '
        'this or similar assignments. Use these examples only to '
        'calibrate your tone, length, and marking standard. Do not '
        'reference them in your output. Apply the same corrections '
        'silently to any similar criteria in this submission.',
        '',
    ]
    for ex in examples:
        orig = _truncate_at_word(ex.get('original_text') or '', 200)
        edited = _truncate_at_word(ex.get('edited_text') or '', 200)
        lines.append(f'Original AI feedback: "{orig}"')
        lines.append(f'Teacher changed it to: "{edited}"')
        if ex.get('theme_key'):
            lines.append(f"Mistake type: {ex['theme_key']}")
        if ex.get('match_tier') == 0:
            lines.append('Context: same assignment, same rubric')
        else:
            lines.append('Context: different assignment, same subject and mistake type')
        lines.append('')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines)
