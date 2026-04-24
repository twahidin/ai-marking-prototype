import os
import logging
import base64
import json
import re
import io
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
        # Use streaming to avoid 10-minute timeout on large requests
        with client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": messages_content}],
            system=system_prompt
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


def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages,
                          script_pages, review_section, marking_section, total_marks):
    """Build system prompt and content for rubrics/essay marking."""
    reference_section = ""
    if reference_pages:
        reference_section = "\nREFERENCE MATERIALS (sample works or other references) have been provided — use them to calibrate your expectations."

    system_prompt = f"""You are an experienced teacher marking a student's essay/extended response using rubrics.

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
- For any criterion where marks_awarded < marks_total, include "correction_prompt": "[Criterion name]: You ... . In your own words, explain what you should have written and why." Concrete, not generic. Omit on full-marks criteria.

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
            "feedback": "diagnostic feedback — 1-2 sentences, no filler",
            "improvement": "specific actions to reach the next band",
            "correction_prompt": "OMIT if marks_awarded == marks_total; otherwise: '[Criterion name]: You ... . In your own words, explain what you should have written and why.'"
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

    _append_pages(content, "\nSTUDENT SCRIPT (evaluate this essay):", script_pages)

    content.append({"type": "text", "text": "\nEvaluate this essay against the rubrics and identify line-by-line errors. Provide JSON feedback:"})

    return system_prompt, content


def _build_short_answer_prompt(subject, rubrics_pages, answer_key_pages, question_paper_pages,
                               script_pages, review_section, marking_section, scoring_mode, total_marks):
    """Build system prompt and content for short answer marking."""
    rubrics_section = ""
    if rubrics_pages:
        rubrics_section = "\nGRADING RUBRICS have been provided — use them to evaluate subjective answers."

    tiered_feedback_instructions = """

TIERED FEEDBACK FOR THE STUDENT — mandatory, not optional.

You MUST produce, in addition to the per-question fields below, a two-row "verdict" at the top of the JSON:

  "well_done": one sentence naming ONE specific thing the student got right. No generic phrases like "good effort" or "you showed understanding". Point at the actual answer.
  "main_gap": one sentence (≤ 30 words) naming the SINGLE most important gap, specific enough that the student knows exactly what to fix.

For EACH question's "feedback" field, use this discipline:
  - Maximum 2 sentences.
  - First sentence: what was present or correct (if anything).
  - Second sentence: what was missing or wrong and why marks were lost.
  - No filler. No restating the criterion name.
  - Never use: "well done", "good attempt", "you demonstrated", "it is important to note", "however", "overall".

For any question where marks_awarded < marks_total, also include:
  "correction_prompt": a one-line prompt in the form "[Question label]: You [specific thing that was missing]. In your own words, explain what you should have written and why."
  Make the "[specific thing that was missing]" concrete — not generic.
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
            "feedback": "diagnostic feedback — 1 to 2 sentences, no filler",
            "improvement": "recommended action for improvement",
            "correction_prompt": "OMIT if marks_awarded == marks_total; otherwise: '[Question label]: You ... . In your own words, explain what you should have written and why.'"
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
            "feedback": "diagnostic feedback — 1 to 2 sentences, no filler",
            "improvement": "recommended action for improvement",
            "correction_prompt": "OMIT if status == 'correct'; otherwise: '[Question label]: You ... . In your own words, explain what you should have written and why.'"
        }}"""

    system_prompt = f"""You are an experienced teacher marking a student's assignment script.

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
                session_keys=None):
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
            review_section, marking_section, total_marks
        )
    else:
        system_prompt, content = _build_short_answer_prompt(
            subject, rubrics_pages, answer_key_pages, question_paper_pages, script_pages,
            review_section, marking_section, scoring_mode, total_marks
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
    """Generate Layer 3 'The idea' + 'Next time' for one criterion.

    Returns: {"idea": str, "next_time": str}
    """
    system_prompt = (
        "You help a student understand WHY this criterion matters.\n\n"
        "Return JSON with exactly two fields — and nothing else:\n"
        '  "idea": ONE sentence. Anchor it in what the question is actually asking for '
        'or in the underlying concept being applied — how knowledge is being used here. '
        'Do NOT frame it around examiners, markers, markschemes or what "examiners want". '
        'Do NOT start with "Examiners want", "Markers look for", "This criterion tests", '
        'or similar meta phrasing. Do NOT restate the criterion name. Speak as if explaining '
        "to the student directly why this part of the answer matters for the question.\n"
        '  "next_time": ONE sentence. A concrete self-check the student can use independently '
        'next time. MUST start with "Next time:" and end with a specific action.\n\n'
        "The two sentences combined must be ≤ 40 words. No fluff, no encouragement phrases, "
        "no repetition of the criterion name."
    )
    user_prompt = (
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Student's answer (excerpt): {(student_answer or '')[:600]}\n"
        f"Expected answer / band descriptor (excerpt): {(expected_answer or '')[:600]}\n"
        f"Teacher's feedback on this criterion: {(feedback_sentence or '')[:400]}\n\n"
        "Return the JSON now."
    )
    parsed = _run_feedback_helper(provider, model, session_keys, system_prompt, user_prompt, max_tokens=300)
    idea = (parsed.get('idea') or '').strip()
    nxt = (parsed.get('next_time') or '').strip()
    # Enforce "Next time:" prefix if AI forgot.
    if nxt and not nxt.lower().startswith('next time'):
        nxt = 'Next time: ' + nxt
    return {'idea': idea, 'next_time': nxt}


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
    parsed = _run_feedback_helper(provider, model, session_keys, system_prompt, user_prompt, max_tokens=200)
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


def categorise_mistakes(provider, model, session_keys, subject_family, themes, questions_data):
    """Async-friendly combined call: for each criterion-with-marks-lost,
    assign a theme_key + specific_label + (optional) low_confidence +
    a themed_correction_prompt. Also generate one group_habit per theme
    that has ≥ 2 criteria assigned (excluding never_group themes).

    `themes` is the THEMES dict from config/mistake_themes.py — passed in
    so this file stays free of hardcoded theme data. `questions_data` is
    a list of dicts with keys: criterion_id, criterion_name, student_answer,
    feedback, marks_awarded, marks_total (or marks_lost).

    Returns a dict: {
        "categorisation": [ {criterion_id, theme_key, specific_label,
                             low_confidence, themed_correction_prompt}, ... ],
        "group_habits":    [ {theme_key, habit}, ... ]
    }

    Raises on API/parse failure — caller marks the submission "failed".
    """
    if not questions_data:
        return {'categorisation': [], 'group_habits': []}

    # Render the themes dict for the prompt — description only, plus the key.
    theme_lines = []
    for key, cfg in themes.items():
        theme_lines.append(
            f"  {key}: {cfg.get('label', key)} — {cfg.get('description', '')}"
            + ("  (never_group)" if cfg.get('never_group') else '')
        )
    theme_block = '\n'.join(theme_lines)
    theme_keys_csv = ', '.join(themes.keys())

    # Render the criteria for the prompt.
    crit_lines = []
    for q in questions_data:
        cid = q.get('criterion_id') or ''
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

    system_prompt = f"""You categorise a student's lost-mark criteria on a Singapore secondary school assignment.

Subject family: {subject_family_str}

The available parent themes (use EXACTLY one of these keys — no others):
{theme_block}

Allowed theme_keys: {theme_keys_csv}

For EACH criterion below, do all of the following:

1. Assign it to exactly one theme_key from the list above. Never leave a criterion unassigned.
2. Generate a specific_label — a 2–4 word phrase from the student's perspective describing THIS particular mistake, not the category. Examples: "consequence not stated", "source not quoted", "informal word choice", "steps in wrong order". The specific_label MUST be distinct from the theme's own label — it describes the instance, not the family.
3. If genuinely torn between two themes, pick the one that leads to the more actionable advice for the student. Set low_confidence: true in that case.
4. Generate a themed_correction_prompt: a single-line prompt framed THROUGH the theme's lens (not generic), telling the student to retry this criterion in their own words. Format:
       "[Criterion]: You [specific thing that was missing]. In your own words, explain what you should have written and why."
   Scope the wording to the chosen theme — e.g. for reasoning_gap, ask them to make the missing link explicit; for evidence_handling, ask them to quote and interpret.

Then, once every criterion is assigned:

5. For each theme that ends up with 2 OR MORE criteria assigned — and that is NOT a never_group theme — produce a SINGLE group_habit sentence that applies to the WHOLE group, not any one question. It must start with "Next time:" and be at most 20 words. Concrete, actionable. One per qualifying group. Omit groups with <2 criteria and groups whose theme has never_group=true.

Respond ONLY with valid JSON in this exact shape (no prose, no markdown):

{{
  "categorisation": [
    {{
      "criterion_id": "<copied>",
      "theme_key": "<one of the allowed keys>",
      "specific_label": "<2-4 words>",
      "low_confidence": false,
      "themed_correction_prompt": "<one line>"
    }}
  ],
  "group_habits": [
    {{
      "theme_key": "<one of the allowed keys>",
      "habit": "Next time: ..."
    }}
  ]
}}"""

    user_prompt = f"Criteria to categorise:\n{crits_block}\n\nReturn the JSON now."

    parsed = _run_feedback_helper(provider, model, session_keys, system_prompt, user_prompt, max_tokens=2000)

    cats_in = parsed.get('categorisation') or []
    habs_in = parsed.get('group_habits') or []
    valid_keys = set(themes.keys())

    cats_out = []
    known_ids = {q.get('criterion_id') for q in questions_data}
    seen_ids = set()
    for c in cats_in:
        if not isinstance(c, dict):
            continue
        cid = c.get('criterion_id')
        if cid is None or cid not in known_ids or cid in seen_ids:
            continue
        seen_ids.add(cid)
        tk = c.get('theme_key')
        if tk not in valid_keys:
            # Coerce into a sensible default so the render layer can still run.
            tk = 'content_gap'
        spec = (c.get('specific_label') or '').strip()
        if not spec:
            spec = (themes.get(tk) or {}).get('label', 'Area for review')
        prompt = (c.get('themed_correction_prompt') or '').strip()
        cats_out.append({
            'criterion_id': cid,
            'theme_key': tk,
            'specific_label': spec,
            'low_confidence': bool(c.get('low_confidence')),
            'themed_correction_prompt': prompt,
        })

    habs_out = []
    seen_theme = set()
    for h in habs_in:
        if not isinstance(h, dict):
            continue
        tk = h.get('theme_key')
        if tk not in valid_keys or (themes.get(tk) or {}).get('never_group'):
            continue
        if tk in seen_theme:
            continue
        habit = (h.get('habit') or '').strip()
        if not habit:
            continue
        if not habit.lower().startswith('next time'):
            habit = 'Next time: ' + habit
        seen_theme.add(tk)
        habs_out.append({'theme_key': tk, 'habit': habit})

    return {'categorisation': cats_out, 'group_habits': habs_out}
