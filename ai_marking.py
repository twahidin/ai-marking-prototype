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
    """Get API key from session keys (if provided) or environment."""
    env_name = PROVIDER_KEY_MAP.get(provider)
    if session_keys and session_keys.get(provider):
        return session_keys[provider]
    return os.getenv(env_name) if env_name else None


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

Respond ONLY with valid JSON:
{{
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
            "feedback": "detailed feedback referencing the specific rubric band",
            "improvement": "specific actions to reach the next band"
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

    if scoring_mode == 'marks':
        total_marks_str = total_marks or '100'
        scoring_instructions = f"""SCORING: Award numerical marks for each question.
Total Marks for this assessment: {total_marks_str}
- Award marks out of each question's total based on correctness and completeness
- Also assign a status: "correct", "partially_correct", or "incorrect"
- Include "marks_awarded" and "marks_total" for each question"""

        question_schema = """{{
            "question_num": 1,
            "student_answer": "transcribed answer from the script",
            "correct_answer": "answer from the answer key",
            "status": "correct | partially_correct | incorrect",
            "marks_awarded": number,
            "marks_total": number,
            "feedback": "specific constructive feedback",
            "improvement": "recommended action for improvement"
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
            "feedback": "specific constructive feedback",
            "improvement": "recommended action for improvement"
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

HANDWRITING RULES:
- IGNORE crossed-out or struck-through text — treat as deleted
- A caret (^) or insertion mark means the student wants to INSERT text at that point
- Focus on the student's FINAL intended answer, not drafts or corrections

FORMATTING:
- Use LaTeX in $ delimiters for math: $x^2 + 3x = 0$
- Use $$ for display equations: $$E = mc^2$$

Respond ONLY with valid JSON:
{{
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
