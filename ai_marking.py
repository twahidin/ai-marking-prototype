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
# explain_criterion, evaluate_correction. Tier-2 is plenty for these.
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


# Shared rules for the per-question "correction_prompt" string. The prompt
# is what the student sees in their feedback view as the task to attempt —
# it must scaffold their thinking towards the correct answer, NEVER hand it
# to them. Three non-negotiables: (1) anchor in the student's actual words,
# (2) point at the gap without revealing the answer or the method, (3) end
# with a concrete thinking step the student can do without being told what
# the right answer is.
CORRECTION_PROMPT_RULES = """CORRECTION PROMPT RULES (for the "correction_prompt" field)

The correction prompt is a SCAFFOLDED THINKING TASK — one short instruction
that helps the student notice WHY their answer fell short and rework it
themselves. It must NOT hand them the correct answer, the correct method,
or the correct value. Make them think; don't make them copy.

EVERY correction prompt MUST satisfy all three:
1. ANCHOR — quote or paraphrase a specific element from THIS student's
   actual answer (a value they wrote, a term they used, a sentence they
   produced). No generic stems. The student must see "this is about MY
   answer", not "this is the same prompt the AI gives everyone".
2. POINT WITHOUT REVEALING — direct attention to the gap (a missing link,
   an inconsistent value, an undefined term, an imprecise word) WITHOUT
   stating the correct answer or the correct method. Frame as a question
   the student answers, or a check the student performs.
3. CONCRETE NEXT STEP — end with one action the student can do now: re-
   derive, re-check, compare, look up, rewrite, sketch, justify. The action
   must be specific to the gap, not "review your work".

VARY the form by mistake type — pick exactly ONE per criterion:

- Procedural / careless slip (arithmetic, unit, missed step):
  "You wrote '[the wrong value or step]'. Re-read what the question gave
  you — which input or step doesn't line up? Re-derive that step."

- Reasoning gap (link not made, cause→effect missing, comparison flat):
  "Your answer says '[the student's claim]' but doesn't say why. What must
  hold between '[X]' and '[Y the question asks about]' for that claim to
  follow? Write the missing step."

- Evidence / source handling (quotation missing, source not unpacked):
  "Your answer mentions '[Z]' without grounding it. Find one phrase in
  '[the source / question]' that connects to '[Z]', and explain how."

- Content / concept gap (term defined wrongly, idea misunderstood):
  "Your answer treats '[the concept]' as '[what the student wrote]'. Look
  up '[the concept]' — does that match how this question is using it?
  Reconcile the two definitions in your own words."

- Language / expression (clarity, register, sentence structure):
  "Your sentence reads '[the student's exact phrase]'. Which word is doing
  the heavy lifting but is too vague? Rewrite using a more precise term."

BANNED — these reveal the answer / method:
- "Re-do the calculation using [the correct method/formula/value]." — gives the method.
- "The correct answer is [Y]; explain why." — gives the answer.
- "Use [formula X] to compute [Y]." — gives both.
- "Convert your answer to [the correct unit]." — gives the unit.
- "In your own words, explain what you should have written and why." — old boilerplate, banned.

BANNED — these are too generic:
- Any prompt that doesn't quote or paraphrase the student's actual answer.
- "Review your working" / "Check your answer" / "Re-read the question" with no anchor.

CONSTRAINTS:
- ≤ 30 words. One sentence (or one short question + one action).
- Across all correction_prompt fields in the SAME response, no two prompts
  may end with the same words. Vary the verb AND the anchor.
- OMIT this field entirely on full-marks (or "correct") criteria."""


# Shared rules for the per-question "idea" string. Inlined into the marking
# response (instead of a separate AI call when the student clicks "Why does
# this matter?") so the explanation is ready the moment the page loads.
IDEA_RULES = """LAYER 3 IDEA RULES (for the "idea" field)

The idea is a one-sentence explanation of WHY this criterion matters,
written for the student. It powers the "Why does this matter?" expander
on the student feedback view.

CONSTRAINTS:
- ≤ 25 words. One sentence.
- Anchor it in what the question is actually asking for or in the
  underlying concept being applied — how knowledge is being used here.
- Do NOT frame it around examiners, markers, markschemes, or what
  "examiners want". Banned openers: "Examiners want", "Markers look
  for", "This criterion tests", "The marker expects".
- Do NOT restate the criterion name.
- Speak directly to the student about why this part of the answer
  matters for the question.
- ALWAYS include the field, even on full-marks criteria — the student
  may still expand the explainer to learn the concept."""


# Mother-tongue subject families whose feedback should be written in the
# native language. Other subjects (English, Math, Sciences, Humanities,
# Lit, Art, Music, NFS, etc.) keep English feedback regardless of how the
# Subject string was typed by the teacher.
MOTHER_TONGUE_LANGUAGES = {
    'chinese': '中文 (Chinese)',
    'malay':   'Bahasa Melayu (Malay)',
    'tamil':   'தமிழ் (Tamil)',
    'hindi':   'हिन्दी (Hindi)',
}


def _language_directive(subject_text):
    """Return a system-prompt block that forces same-language feedback for
    mother-tongue assignments. Returns '' for non-mother-tongue subjects so
    the prompt remains unchanged for English/Math/Sciences/etc.

    Resolves the freeform subject via subjects.resolve_subject_key (the same
    canonical mapping used by the dropdown / autocomplete), so 'CL', 'higher
    chinese', 'Chinese Language', etc. all map to the chinese directive.
    """
    from subjects import resolve_subject_key
    key = resolve_subject_key(subject_text or '')
    lang = MOTHER_TONGUE_LANGUAGES.get(key)
    if not lang:
        return ''
    return f"""LANGUAGE OF FEEDBACK — STRICT.
This assignment's subject is a mother-tongue language: {lang}. ALL
student-facing prose in your JSON output MUST be written in {lang}, in
grammatically correct, age-appropriate {lang} (the level a Singapore
secondary student should be able to read). The student's script is in
{lang}; do not switch to English just because this system prompt happens
to be written in English.

WRITE IN {lang}:
- well_done, main_gap, overall_feedback
- For each question / criterion: feedback, improvement, idea,
  correction_prompt, student_answer, correct_answer
- Each item in recommended_actions
- errors[].original and errors[].correction (mirror the student's exact text)
- errors[].location

KEEP IN ENGLISH (controlled vocabulary the platform depends on — do NOT translate):
- "status" values: "correct" / "partially_correct" / "incorrect"
- "type" values for errors: grammar | spelling | punctuation | vocabulary | factual | logical | style

KEEP VERBATIM FROM THE SOURCE DOCUMENT (do not translate either way):
- "criterion_name" — copy exactly from the rubrics table heading,
  whatever language it is written in.
- "band" — copy exactly as it appears in the rubrics
  (e.g. "Band 5 (17-20)").

FORMATTING NOTES:
- Math stays inside $...$ delimiters with English LaTeX commands
  (\\frac, \\times, \\leq, etc.). Do not translate math.
- For Chinese: use Simplified Chinese (简体中文) by default. If the student's
  script is clearly written in Traditional Chinese, mirror their variant.
- For Tamil / Malay / Hindi: use modern, standard orthography.
- The word / character limits in the feedback rules apply by character count
  for Chinese, by word count for Tamil / Malay / Hindi.

QUOTATION MARKS — STRICT (this prevents JSON parse failures):
- NEVER use ASCII straight double quotes (") inside any string value. They
  collide with the JSON delimiters and corrupt your output.
- For Chinese, when quoting a word, phrase, or idiom inline, use 「」 (corner
  brackets) or “ ” (full-width curly quotes, U+201C / U+201D). Example:
  「因为」与「所以」应成对出现。
- For Tamil / Malay / Hindi, use the curly quotes “ ” (U+201C / U+201D), not
  ASCII " ".
- If you must use ASCII " for any reason, you MUST escape it as \\".

NO INLINE ANNOTATIONS — STRICT.
- Do NOT insert pinyin, romanisation, transliteration, or any other gloss
  inside the prose using angle-bracket tags like <yong>, <pinyin>, <gloss>,
  <ruby>, etc. The platform generates pinyin annotations automatically
  AFTER you respond; your job is to write clean mother-tongue prose only.
- Do NOT use parenthetical pinyin either, e.g. "用 (yòng)". Just write 用.
- Do NOT emit any HTML tags (<b>, <i>, <em>, <span>, etc.) inside string
  values. Plain prose only.

Self-check before responding: re-read every field listed above. If any
student-facing field is still in English, rewrite it in {lang}.

"""


def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages,
                          script_pages, review_section, marking_section, total_marks,
                          calibration_block=''):
    """Build system prompt and content for rubrics/essay marking."""
    reference_section = ""
    if reference_pages:
        reference_section = "\nREFERENCE MATERIALS (sample works or other references) have been provided — use them to calibrate your expectations."

    language_block = _language_directive(subject)

    system_prompt = f"""{calibration_block}{language_block}You are an experienced teacher marking a student's essay/extended response using rubrics.

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

{IDEA_RULES}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Wrap ALL math in $ delimiters so it renders as proper symbols/fractions in the report.
- Use \frac for fractions: $\frac{{1}}{{2}}$ (NOT 1/2). Use ^{{ }} for powers: $x^{{2}}$.
- Use \times for multiplication, \div for division, \leq / \geq for inequalities, \pi for pi, \sqrt{{ }} for square roots.
- Examples: $\frac{{3}}{{4}}$, $x^{{2}} + 3x = 0$, $5 \times 3 = 15$, $\sqrt{{16}} = 4$.

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
            "idea": "single sentence — see LAYER 3 IDEA RULES (≤25 words, why this criterion matters)",
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
            "idea": "single sentence — see LAYER 3 IDEA RULES (≤25 words, why this question matters)",
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
            "idea": "single sentence — see LAYER 3 IDEA RULES (≤25 words, why this question matters)",
            "correction_prompt": "OMIT if status == 'correct'; otherwise one short do-this-now task following CORRECTION PROMPT RULES — pick the form matching this question's mistake type (procedural / reasoning / evidence / concept / language). ≤ 25 words. Must not duplicate another question's wording."
        }}"""

    language_block = _language_directive(subject)

    system_prompt = f"""{calibration_block}{language_block}You are an experienced teacher marking a student's assignment script.

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

{IDEA_RULES}

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Wrap ALL math in $ delimiters so it renders as proper symbols/fractions in the report.
- Use \frac for fractions: $\frac{{1}}{{2}}$ (NOT 1/2). Use ^{{ }} for powers: $x^{{2}}$.
- Use \times for multiplication, \div for division, \leq / \geq for inequalities, \pi for pi, \sqrt{{ }} for square roots.
- Use $$ for centered display equations: $$E = mc^2$$.
- Examples: $\frac{{3}}{{4}}$, $x^{{2}} + 3x = 0$, $5 \times 3 = 15$, $\sqrt{{16}} = 4$.

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
- Wrap ALL math in $ delimiters so it renders as proper symbols/fractions in the report.
- Use \frac for fractions: $\frac{{1}}{{2}}$ (NOT 1/2). Use ^{{ }} for powers: $x^{{2}}$.
- Use \times for multiplication, \div for division, \leq / \geq for inequalities, \pi for pi, \sqrt{{ }} for square roots.
- Examples: $\frac{{3}}{{4}}$, $x^{{2}} + 3x = 0$, $5 \times 3 = 15$, $\sqrt{{16}} = 4$.

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
                session_keys=None, calibration_block='', pinyin_mode='off'):
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

        # Pinyin annotation: when the assignment is Chinese AND the teacher
        # opted in via Assignment.pinyin_mode, add ruby-annotated HTML
        # siblings ('feedback_html', 'improvement_html', etc.) alongside
        # the raw Chinese fields. Templates render the _html version when
        # present and fall back to the raw text otherwise — old submissions
        # are unaffected.
        if pinyin_mode and pinyin_mode != 'off':
            from subjects import resolve_subject_key
            if resolve_subject_key(subject or '') == 'chinese':
                try:
                    from pinyin_annotate import annotate_result_for_pinyin
                    annotate_result_for_pinyin(result, pinyin_mode)
                    result['pinyin_mode'] = pinyin_mode
                except Exception as _e:
                    logger.warning(f'pinyin annotation skipped: {_e}')

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
# Tiered feedback helpers (student-facing) — feed_forward_beta layer-3
# explain/evaluate calls. Tier-2 model is plenty for these short
# JSON-classification tasks.
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
    """Single-shot chat completion that parses the first {...} block from
    the response as JSON. Used by extract_correction_insight,
    refresh_criterion_feedback, and any other tier-2 JSON helper."""
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



def extract_correction_insight(provider, model, session_keys,
                                subject, theme_key,
                                criterion_name, original_text, edited_text):
    """Extract a reusable marking principle from a teacher's correction.

    Returns {mistake_pattern, correction_principle, transferability} or None
    on failure. Caller writes the three fields back to the originating
    feedback_edit row. Cheap-tier model via HELPER_MODELS.
    """
    system_prompt = (
        "You extract a reusable marking principle from a teacher's correction.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "mistake_pattern": "2-4 word phrase naming the type of error in '
        'the original feedback — diagnostic, not advice",\n'
        '  "correction_principle": "one sentence describing what this '
        "teacher's edit reveals about their marking standard — what they "
        'always do, never do, or consistently prefer",\n'
        '  "transferability": "high | medium | low"\n'
        "}\n\n"
        "transferability:\n"
        "  high   = applies to any similar question in any assignment\n"
        "  medium = applies within this subject\n"
        "  low    = specific to this question or assignment type only\n\n"
        "The correction_principle must be written as a generalised rule, not "
        "a description of this specific edit. It should read like something a "
        "new teacher could follow without seeing the original scripts.\n\n"
        'WRONG: "The teacher added a reference to genetic identity."\n'
        'RIGHT: "Always name the specific missing consequence rather than '
        'asking students to explain further."\n\n'
        "Maximum 30 words for correction_principle."
    )
    user_prompt = (
        f"Subject: {subject or 'unknown'}\n"
        f"Theme: {theme_key or 'unknown'}\n"
        f"Criterion: {criterion_name}\n"
        f"Original AI feedback: {(original_text or '')[:600]}\n"
        f"Teacher's edited feedback: {(edited_text or '')[:600]}\n\n"
        "Return the JSON now."
    )
    helper_model = _helper_model_for(provider, model)
    try:
        parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                       system_prompt, user_prompt, max_tokens=200)
    except Exception as e:
        logger.warning(f"extract_correction_insight failed: {e}")
        return None
    transferability = (parsed.get('transferability') or '').strip().lower()
    if transferability not in ('high', 'medium', 'low'):
        transferability = None
    pattern = (parsed.get('mistake_pattern') or '').strip()[:80] or None
    principle = (parsed.get('correction_principle') or '').strip()[:300] or None
    return {
        'mistake_pattern': pattern,
        'correction_principle': principle,
        'transferability': transferability,
    }


def refresh_criterion_feedback(provider, model, session_keys, subject,
                                criterion_name, student_answer, correct_answer,
                                marks_awarded, marks_total, calibration_edit):
    """Regenerate feedback + improvement for one criterion on one student,
    calibrated against a teacher's edit on another student. Text-only call —
    no images, no full marking pipeline. Cheap-tier model via HELPER_MODELS.
    Returns {feedback, improvement}.
    """
    helper_model = _helper_model_for(provider, model)
    system_prompt = (
        "You are regenerating feedback for one criterion on a student's "
        "script. A teacher has shown you their marking standard by editing "
        "another student's feedback on the same type of mistake.\n\n"
        "Apply the same standard to this student's answer. Do not change "
        "the marks. Do not re-evaluate correctness. Only rewrite the "
        "Feedback and Suggested Improvement fields.\n\n"
        f"{FEEDBACK_GENERATION_RULES}\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "feedback": "...",\n'
        '  "improvement": "..."\n'
        "}"
    )
    orig = (calibration_edit.original_text or '')[:200]
    edited = (calibration_edit.edited_text or '')[:200]
    principle_line = ''
    cp = getattr(calibration_edit, 'correction_principle', None)
    if cp:
        principle_line = f"\nTeacher's principle: \"{cp}\""
    user_prompt = (
        "TEACHER'S CALIBRATION EDIT (apply this standard):\n"
        f"Original AI feedback: \"{orig}\"\n"
        f"Teacher changed it to: \"{edited}\"{principle_line}\n\n"
        "NOW APPLY THE SAME STANDARD TO:\n"
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Student's answer: {(student_answer or '')[:600]}\n"
        f"Expected answer: {(correct_answer or '')[:400]}\n"
        f"Marks: {marks_awarded if marks_awarded is not None else '-'} / {marks_total if marks_total is not None else '-'}\n\n"
        "Return the JSON now."
    )
    parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                   system_prompt, user_prompt, max_tokens=300)
    feedback = (parsed.get('feedback') or '').strip()
    improvement = (parsed.get('improvement') or '').strip()
    return {'feedback': feedback, 'improvement': improvement}


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



# Advice-language detector for specific_label sanitisation. Single-pass
# categorisation no longer has a verification AI call (Pass 2), so this
# regex catches the obvious "check X / always Y" advice patterns and falls
# back to the theme's generic label rather than running another round-trip.
_ADVICE_LABEL_PATTERNS = re.compile(
    r"^\s*(check\b|show\b|remember\b|always\b|never\b|make sure\b|"
    r"read\b|ask\b|review\b|consider\b|ensure\b|don\'?t\b|do not\b|"
    r"try to\b|attempt to\b|aim to\b)",
    re.IGNORECASE,
)


def _looks_like_advice(label):
    """Return True if `label` reads like advice rather than a diagnosis."""
    return bool(label and _ADVICE_LABEL_PATTERNS.match(label.strip()))


# Phrases that signal an answer-revealing or generic boilerplate correction
# prompt. The categorisation prompt instructs the AI to scaffold thinking,
# never to hand over the answer or method; this regex catches the obvious
# violations so the UI falls back to the marking-time prompt instead of
# confusing the student.
_BOILERPLATE_PROMPT_PATTERNS = re.compile(
    r'(?i)(?:'
    r'in your own words[, ]+explain'                  # banned old boilerplate
    r'|the correct answer is\b'                        # gives the answer
    r'|use the formula\b'                              # gives the formula
    r'|use the equation\b'
    r'|the answer should be\b'
    r'|convert (?:your answer|to)\s+(?:to\s+)?[a-z]+\b'  # gives the unit
    r'|^(?:re-?do|redo)\b.*\busing\b'                  # "redo using [method]"
    r')'
)


def _looks_like_boilerplate_correction(prompt):
    """Return True if a themed_correction_prompt looks like generic
    boilerplate or reveals the answer/method directly. Conservative — only
    rejects on clear violations; ambiguous prompts pass through."""
    if not prompt:
        return False
    return bool(_BOILERPLATE_PROMPT_PATTERNS.search(prompt))


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


def fetch_recent_categorisation_corrections(subject, limit=5):
    """Pull up to `limit` most recent teacher corrections for the given
    `subject` string (canonical assignment.subject from the dropdown,
    matched case-insensitively via JOIN). Joined with the original
    criterion content from the source submission's result_json. Returns
    a list of dicts:
      {criterion_text, original_theme_key, corrected_theme_key,
       original_specific_label, corrected_specific_label}

    Returns [] when subject is blank OR isn't a canonical-taxonomy entry
    (subjects.py). Freeform-subject assignments are intra-assignment-
    only by design — they don't contribute to the cross-assignment
    categorisation corpus.

    Cheap: one JOIN'd SELECT for the corrections + one bulk SELECT for
    all referenced submissions. No AI calls.
    """
    from db import db, CategorisationCorrection, Submission, Assignment
    from subjects import is_canonical_subject
    if not subject or not is_canonical_subject(subject):
        return []
    rows = (CategorisationCorrection.query
            .join(Assignment, Assignment.id == CategorisationCorrection.assignment_id)
            .filter(db.func.lower(Assignment.subject) == subject.strip().lower())
            .order_by(CategorisationCorrection.created_at.desc())
            .limit(limit)
            .all())
    if not rows:
        return []
    sids = list({r.submission_id for r in rows if r.submission_id is not None})
    subs_by_id = {}
    if sids:
        for s in Submission.query.filter(Submission.id.in_(sids)).all():
            subs_by_id[s.id] = s

    out = []
    for r in rows:
        try:
            sub = subs_by_id.get(r.submission_id)
            if not sub:
                continue
            result = sub.get_result() or {}
            target_q = None
            for q in (result.get('questions') or []):
                if str(q.get('question_num')) == r.criterion_id:
                    target_q = q
                    break
            if not target_q:
                continue
            criterion_name = (target_q.get('criterion_name') or f'Q{r.criterion_id}').strip()
            feedback = (target_q.get('feedback') or '').strip().replace('\n', ' ')
            student_answer = (target_q.get('student_answer') or '').strip().replace('\n', ' ')
            text = criterion_name
            if feedback:
                text += f' — {feedback[:200]}'
            if student_answer:
                text += f' | student wrote: {student_answer[:160]}'
            if len(text) > 400:
                text = text[:400] + '…'
            out.append({
                'criterion_text': text,
                'original_theme_key': r.original_theme_key,
                'corrected_theme_key': r.corrected_theme_key,
                'original_specific_label': r.original_specific_label,
                'corrected_specific_label': r.corrected_specific_label,
            })
        except Exception:
            continue
    return out


def format_categorisation_corrections_block(corrections):
    """Render the few-shot teacher corrections as a prompt block to prepend
    to the categorisation user prompt. Returns '' when empty so the caller
    can splice unconditionally without size checks.
    """
    if not corrections:
        return ''
    lines = [
        "PAST TEACHER CORRECTIONS (for this subject)",
        "",
        "The AI initially classified these criteria one way and teachers "
        "corrected them. When you encounter criteria below that resemble "
        "these examples, prefer the teacher's classification over the "
        "AI's original judgement.",
        "",
    ]
    for c in corrections:
        lines.append(f"- Criterion: \"{c['criterion_text']}\"")
        lines.append(f"  AI initially said: {c.get('original_theme_key') or '(none)'}")
        lines.append(f"  Teacher corrected to: {c['corrected_theme_key']}")
        lines.append("")
    return '\n'.join(lines).strip() + '\n\n'


def categorise_mistakes(provider, model, session_keys, subject, themes, questions_data, corrections_block=''):
    """Single-pass categorisation pipeline.

    One AI call produces:
      - Per-criterion theme_key + diagnostic specific_label
      - Per-group "Next time:" habit (folded into the same JSON)

    The diagnose-not-advise discipline lives entirely in the prompt. A
    Python-side regex (`_looks_like_advice`) catches obvious advice
    patterns post-parse and falls back to the theme's generic label
    rather than running another AI verification round.

    `themes` is the THEMES dict from config/mistake_themes.py — passed in
    so this module never hardcodes theme data. `questions_data` is a list
    of dicts with criterion_id, criterion_name, student_answer, feedback,
    marks_awarded, marks_total (or marks_lost).

    Returns: {
        "categorisation": [ {criterion_id, theme_key, specific_label,
                             low_confidence, themed_correction_prompt: ""}, ... ],
        "group_habits":    [ {theme_key, habit}, ... ]
    }

    Raises on the single AI call's failure (caller marks the submission
    "failed").
    """
    import json as _json
    if not questions_data:
        return {'categorisation': [], 'group_habits': []}

    valid_keys = list(themes.keys())
    valid_keys_set = set(valid_keys)

    # ---- Render shared blocks for the prompt ----
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

    subject_str = subject or 'unknown'

    # ---------------------------------------------------------------
    # SINGLE PASS — diagnose, group, label, habit (all in one JSON)
    # ---------------------------------------------------------------
    system_prompt = f"""You are analysing a student's mistakes across an assignment to identify shared causes, produce calibrated category labels, and propose one "Next time" habit per group.

Subject: {subject_str}

Allowed parent themes (use EXACTLY one of these keys — no others):
{theme_block}

Allowed theme_keys: {theme_keys_csv}

DISCIPLINE — diagnose, do not advise.

A specific_label names what went WRONG. It must NOT tell the student what to do.

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

A label must be specific enough that two different error types would get two different labels. Generic labels that could apply to any subject or any mistake are not acceptable.

DISCIPLINE — habits, not platitudes.

Each "Next time:" habit is ONE concrete self-check the student can run independently. Maximum 20 words INCLUDING "Next time:". Must name a specific action tied to this group's error pattern, not generic study advice.

WRONG: "Next time: check your work before submitting."
WRONG: "Next time: be more careful with your answers."
RIGHT: "Next time: after writing any quantity, ask yourself — have I stated the unit?"
RIGHT: "Next time: after describing a process, ask — what breaks if this step fails?"

DISCIPLINE — themed correction prompts MUST scaffold thinking, NOT reveal the answer.

For each categorised criterion, also produce a `themed_correction_prompt`: a
short instruction the student sees in their feedback view that helps them
notice WHY their answer fell short and rework it themselves. You have just
diagnosed the theme — use it to frame the prompt, but do NOT hand them the
correct answer, the correct method, the correct value, or the correct unit.

EVERY themed_correction_prompt MUST satisfy all three:
1. ANCHOR — quote or paraphrase a specific element from THIS student's actual
   answer (a value they wrote, a term they used, a sentence they produced).
   No generic stems.
2. POINT WITHOUT REVEALING — direct attention to the gap WITHOUT stating the
   correct answer or method. Frame as a question the student answers, or a
   check they perform. The student must do the cognitive work.
3. CONCRETE NEXT STEP — end with one action the student can do now: re-
   derive, re-check, compare, look up, rewrite, sketch, justify. Specific to
   the gap, not "review your work".

ALIGN the prompt with the diagnosed theme:
- careless_slip / procedural_error: anchor in the wrong value or step the student wrote, then ask them to re-read the question's inputs and re-derive — never name the right method.
- misread_question / incomplete_answer: anchor in what the student answered, then ask what the question is actually asking for — never restate the question for them.
- content_gap / misconception: anchor in how the student used the concept, then ask them to look it up and reconcile — never define it for them.
- keyword_missing / too_vague / language_error: anchor in the student's exact phrase, then ask which word is imprecise / which technical term is missing — never supply the missing term.
- working_not_shown / mark_allocation_ignored / question_format_not_followed: anchor in what the student produced (the short answer, the wrong format), then ask what the question's format demanded — never describe the missing structure for them.

BANNED in themed_correction_prompt:
- "Re-do the calculation using [the correct method]." — gives the method.
- "The correct answer is [Y]; explain why." — gives the answer.
- "Convert to [the correct unit]." — gives the unit.
- "Use the formula [X]." — gives the formula.
- "In your own words, explain what you should have written and why." — boilerplate.
- Any prompt that doesn't quote or paraphrase THIS student's actual answer.

CONSTRAINTS for themed_correction_prompt:
- ≤ 30 words. One sentence (or short question + action).
- No two themed_correction_prompts in the same response may end with the same words.

PROCESS

1. For each criterion that lost marks, write a one-sentence diagnosis (what went wrong, not what to do).
2. Find shared causes across diagnoses. A group needs ≥ 2 criteria sharing the same root error.
3. For each group, generate a 2-4 word diagnostic specific_label, assign ONE theme_key, and write one "Next time:" habit.
4. Standalone criteria (no shared cause) still get a theme_key and a 2-4 word specific_label, but no group entry and no habit.
5. For EVERY criterion (grouped or standalone), write a themed_correction_prompt following the rules above.

OUTPUT

After your reasoning, emit ONLY this JSON, tagged with the literal token FINAL_JSON: on its own line:

FINAL_JSON:
{{
  "categorisation": [
    {{
      "criterion_id": "<copied>",
      "theme_key": "<one of {theme_keys_csv}>",
      "specific_label": "<2-4 words, diagnostic>",
      "themed_correction_prompt": "<scaffolded thinking task — anchored, non-revealing, ≤ 30 words>",
      "low_confidence": false
    }}
  ],
  "groups": [
    {{
      "theme_key": "<one of {theme_keys_csv}>",
      "specific_label": "<2-4 words, diagnostic>",
      "habit": "Next time: <one self-check, ≤ 20 words including 'Next time:'>",
      "criteria_ids": ["<criterion_id>", "<criterion_id>"]
    }}
  ]
}}

Rules for the JSON:
- Every criterion with marks lost appears in categorisation exactly once.
- low_confidence: true ONLY if you were genuinely uncertain between two themes.
- A group appears in groups ONLY if it has 2 or more criteria.
- Standalone criteria appear in categorisation but NOT in groups.
- Self-check before output: re-read each specific_label. Does it start with a verb like "check", "show", "remember", "always", "read"? If so, rewrite as a diagnosis (a noun phrase like "X error" or "X omitted").
- Self-check before output: re-read each themed_correction_prompt. Does it (a) quote/paraphrase the student's actual answer? (b) avoid stating the correct answer/method/value/unit? (c) end with a concrete thinking action? If any answer is "no", rewrite.
- Never leave a criterion unassigned."""

    user_prompt = (
        (corrections_block or '') +
        f"Criteria with marks lost:\n{crits_block}\n\n"
        "Work through the process then return FINAL_JSON."
    )

    raw_text = _run_text_completion(provider, model, session_keys, system_prompt, user_prompt, max_tokens=3500)

    try:
        parsed = _extract_final_json(raw_text)
    except Exception as e:
        raise ValueError(f"Categorisation FINAL_JSON could not be parsed: {e}")

    # ---------------------------------------------------------------
    # Sanitise categorisation: enforce theme_key, fall back to theme
    # generic label when the AI emitted advice-style text or left it blank.
    # ---------------------------------------------------------------
    cats_in = parsed.get('categorisation') or []
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
        # Python-side guard: when the AI slips an advice-style phrase past
        # the prompt's discipline, fall back to the theme's generic label
        # rather than running another AI round-trip to verify.
        if not spec or _looks_like_advice(spec):
            spec = (themes.get(tk) or {}).get('label', 'Area for review')
        # Themed correction prompt: scaffolded thinking task that the student
        # sees in their feedback view, anchored in their actual answer and
        # aligned with the diagnosed theme. Reject if it looks like a
        # generic boilerplate or an answer-revealing instruction so the UI
        # falls back to the marking-time prompt rather than confusing the
        # student.
        themed_prompt = (c.get('themed_correction_prompt') or '').strip()
        if themed_prompt and _looks_like_boilerplate_correction(themed_prompt):
            themed_prompt = ''
        cats_out.append({
            'criterion_id': cid,
            'theme_key': tk,
            'specific_label': spec,
            'low_confidence': bool(c.get('low_confidence')),
            'themed_correction_prompt': themed_prompt,
        })

    # ---------------------------------------------------------------
    # Pull group habits straight from the same JSON (Pass 3 absorbed).
    # ---------------------------------------------------------------
    habits_out = []
    seen_theme = set()
    for g in (parsed.get('groups') or []):
        if not isinstance(g, dict):
            continue
        tk = g.get('theme_key')
        if tk not in valid_keys_set or tk in seen_theme:
            continue
        if (themes.get(tk) or {}).get('never_group'):
            continue
        cids = [str(c) for c in (g.get('criteria_ids') or [])]
        if len(cids) < 2:
            continue
        habit = (g.get('habit') or '').strip()
        if not habit:
            continue
        if not habit.lower().startswith('next time'):
            habit = 'Next time: ' + habit
        seen_theme.add(tk)
        habits_out.append({'theme_key': tk, 'habit': habit})

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
    the current marking. Two sub-queries, merged then deduped:

      Tier 0: same assignment + same rubric_version (no theme filter,
              rubric hash already pins us to this assignment's content).
              ALWAYS runs — drives same-assignment propagation regardless
              of whether the subject is canonical or freeform.
      Tier 1: different assignment, exact LOWER(assignments.subject)
              match. ONLY runs when the assignment's subject is a
              canonical-taxonomy entry (subjects.is_canonical_subject).
              Freeform subjects (anything not in the dropdown) are
              intra-assignment-only by design — their feedback edits
              never reach a different assignment's marking. When
              `theme_keys` is non-empty, Tier 1 narrows further to
              edits whose theme_key matches one of the supplied keys.

    `theme_keys` is the iterable of theme_keys from the current
    submission's lost-mark criteria. May be empty (first mark of a fresh
    submission, or submission not yet categorised) — Tier 1 still fires
    on subject match alone in that case.

    All queries use bound parameters via SQLAlchemy text(). Never
    f-string interpolation.
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

    from subjects import is_canonical_subject as _is_canonical_subject
    target_subject = (getattr(assignment, 'subject', '') or '').strip()
    if target_subject and _is_canonical_subject(target_subject):
        # Tier 1: cross-assignment match on canonical subject string.
        # Skipped entirely when the source assignment's subject isn't
        # in the canonical dropdown taxonomy — freeform-subject
        # assignments stay intra-assignment-only (Tier 0 above still
        # picks up same-assignment edits, which drives propagation
        # within the class). If theme_keys is supplied, narrow to
        # those theme_keys; else match all edits within the subject.
        theme_filter = ''
        params = {
            'teacher_id': teacher_id,
            'aid': assignment.id,
            'target_subject': target_subject,
        }
        theme_list = [tk for tk in (theme_keys or []) if tk]
        if theme_list:
            placeholders = ', '.join(f':tk{i}' for i in range(len(theme_list)))
            theme_filter = f' AND fe.theme_key IN ({placeholders})'
            for i, tk in enumerate(theme_list):
                params[f'tk{i}'] = tk
        tier1_sql = _sql_text(
            "SELECT fe.id AS id, fe.original_text AS original_text, "
            "       fe.edited_text AS edited_text, fe.theme_key AS theme_key, "
            "       fe.assignment_id AS assignment_id, "
            "       fe.rubric_version AS rubric_version, "
            "       fe.created_at AS created_at, fe.criterion_id AS criterion_id, "
            "       fe.field AS field, 1 AS match_tier "
            "FROM feedback_edit fe "
            "JOIN assignments a ON a.id = fe.assignment_id "
            "WHERE fe.edited_by = :teacher_id "
            "  AND fe.active = true "
            "  AND fe.assignment_id != :aid "
            "  AND LOWER(a.subject) = LOWER(:target_subject) "
            f"  {theme_filter}"
            " ORDER BY fe.created_at DESC"
        )
        for r in db.session.execute(tier1_sql, params).mappings().all():
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


def count_active_calibration_edits(subject):
    """Count active calibration edits across ALL teachers for the given
    `subject` string (case-insensitive match against assignments.subject
    via JOIN). Used by the calibration-injection threshold gate.

    Returns 0 when subject is blank OR isn't a canonical-taxonomy entry
    (subjects.py). Freeform-subject assignments don't accumulate toward
    the shared principles threshold — they're intra-assignment-only.
    """
    from db import db, FeedbackEdit, Assignment
    from subjects import is_canonical_subject
    if not subject or not is_canonical_subject(subject):
        return 0
    return (db.session.query(FeedbackEdit)
            .join(Assignment, Assignment.id == FeedbackEdit.assignment_id)
            .filter(db.func.lower(Assignment.subject) == subject.strip().lower(),
                    FeedbackEdit.active == True)  # noqa: E712 — SQLAlchemy comparison
            .count())


def get_marking_principles(provider, model, session_keys, subject):
    """Return the shared cached markdown principles file for `subject`
    (canonical Assignment.subject string, e.g. 'Physics', 'Higher
    Chinese'). Cache is keyed on the subject string (case-insensitive
    via SQL lower()).

    Regenerates when:
      1. Cache row missing, OR
      2. is_stale=True AND count_active_calibration_edits >= 8, OR
      3. generated_at is older than 30 days.

    Returns '' (no principles) when:
      - subject is blank, OR
      - subject isn't canonical (freeform assignments don't share
        principles), OR
      - fewer than 8 active calibration edits across the whole subject.
    """
    from db import db, MarkingPrinciplesCache, FeedbackEdit, Assignment
    from subjects import is_canonical_subject

    THRESHOLD = 8
    if not subject:
        return ''
    subject_norm = subject.strip()
    if not subject_norm or not is_canonical_subject(subject_norm):
        return ''
    edit_count = count_active_calibration_edits(subject_norm)
    if edit_count < THRESHOLD:
        return ''

    cache = (MarkingPrinciplesCache.query
             .filter(db.func.lower(MarkingPrinciplesCache.subject) == subject_norm.lower())
             .first())

    needs_regen = False
    if cache is None:
        needs_regen = True
    else:
        if cache.is_stale:
            needs_regen = True
        elif cache.generated_at:
            ga = cache.generated_at
            if ga.tzinfo is None:
                ga = ga.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - ga).total_seconds() > 30 * 86400:
                needs_regen = True
        else:
            needs_regen = True

    if not needs_regen:
        return cache.markdown_text or ''

    edits = (FeedbackEdit.query
             .join(Assignment, Assignment.id == FeedbackEdit.assignment_id)
             .filter(db.func.lower(Assignment.subject) == subject_norm.lower(),
                     FeedbackEdit.active == True)  # noqa: E712
             .all())
    by_theme = {}
    for e in edits:
        tk = e.theme_key or 'unknown'
        by_theme.setdefault(tk, []).append(e)

    summary_lines = []
    for tk, lst in by_theme.items():
        summary_lines.append(f"\n[{tk}] ({len(lst)} edits)")
        for e in lst:
            principle = (e.correction_principle or '').strip()
            if not principle:
                continue
            summary_lines.append(f"- {principle[:150]}")
    summary_block = '\n'.join(summary_lines).strip() or '(no correction_principle text available)'

    system_prompt = (
        "You are summarising a teacher's marking corrections into a concise "
        "principles file. This file will be read by an AI model before marking "
        "new student scripts — write it for that audience, not for the teacher.\n\n"
        "Structure the output as markdown with one section per theme that has "
        "corrections. Each section: a short heading, then one to three bullet "
        "points of principles — generalised rules, not descriptions of specific "
        "edits.\n\n"
        "Rules for writing principles:\n"
        '- Write as imperatives: "Always...", "Never...", "When X, do Y"\n'
        "- Must be specific enough to change marking behaviour\n"
        "- Must not reference specific students, assignments, or dates\n"
        "- Must not exceed 20 words per bullet point\n\n"
        "Where corrections in the same theme conflict — different teachers' "
        "principles pull in opposite directions — take the dominant pattern "
        "(supported by the most edits) and write the principle reflecting it. "
        "If you had to suppress a contradicting principle, set "
        '"has_conflicts": true in your output. Otherwise "has_conflicts": false.\n\n'
        "Maximum total markdown length: 400 words.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "markdown": "...principles file content (markdown, no preamble)...",\n'
        '  "has_conflicts": true | false\n'
        "}"
    )
    user_prompt = (
        f"Subject: {subject_norm}\n"
        f"Total active calibration edits: {edit_count}\n\n"
        "Edits grouped by theme (each line is one teacher's correction principle):\n"
        f"{summary_block}\n\n"
        "Return the JSON now."
    )

    helper_model = _helper_model_for(provider, model)
    try:
        parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                       system_prompt, user_prompt, max_tokens=700)
    except Exception as e:
        logger.warning(f"principles regen failed for {subject_norm}: {e}")
        return (cache.markdown_text if cache else '') or ''

    new_md = (parsed.get('markdown') or '').strip()
    has_conflicts = bool(parsed.get('has_conflicts'))
    if not new_md:
        return (cache.markdown_text if cache else '') or ''

    try:
        if cache is None:
            cache = MarkingPrinciplesCache(
                subject=subject_norm,
                markdown_text=new_md,
                generated_at=datetime.now(timezone.utc),
                is_stale=False,
                edit_count_at_gen=edit_count,
                has_conflicts=has_conflicts,
            )
            db.session.add(cache)
        else:
            cache.markdown_text = new_md
            cache.generated_at = datetime.now(timezone.utc)
            cache.is_stale = False
            cache.edit_count_at_gen = edit_count
            cache.has_conflicts = has_conflicts
        db.session.commit()
        logger.info(f"principles regenerated for {subject_norm}: "
                    f"{edit_count} edits, has_conflicts={has_conflicts}")
    except Exception as commit_err:
        db.session.rollback()
        logger.warning(f"principles regen commit failed: {commit_err}")

    return new_md


def build_calibration_block(teacher_id, asn, subject, theme_keys,
                             provider, model, session_keys):
    """Tiered calibration injection. `subject` is the canonical
    Assignment.subject string from the dropdown (subjects.py).

    < 8 active edits in the subject (across ALL teachers) → existing
    teacher-scoped raw examples (format_calibration_block over
    fetch_calibration_examples).

    >= 8 → shared markdown principles file. On regeneration failure,
    falls back to a smaller raw-example pull (limit=5).
    """
    THRESHOLD = 8
    edit_count = count_active_calibration_edits(subject)
    if edit_count < THRESHOLD:
        return format_calibration_block(
            fetch_calibration_examples(teacher_id, asn, theme_keys, limit=10)
        )

    principles = get_marking_principles(provider, model, session_keys, subject)
    if not principles:
        return format_calibration_block(
            fetch_calibration_examples(teacher_id, asn, theme_keys, limit=5)
        )
    return (
        "---\n"
        "MARKING PRINCIPLES (this subject's established standard)\n\n"
        f"{principles}\n"
        "---\n\n"
    )
