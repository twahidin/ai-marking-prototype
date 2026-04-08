import os
import logging
import base64
import json
import re
import io
from datetime import datetime

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


def get_available_providers():
    """Return dict of provider -> config for providers with API keys configured."""
    available = {}
    if os.getenv('ANTHROPIC_API_KEY'):
        available['anthropic'] = PROVIDERS['anthropic']
    if os.getenv('OPENAI_API_KEY') and OPENAI_AVAILABLE:
        available['openai'] = PROVIDERS['openai']
    if os.getenv('QWEN_API_KEY') and OPENAI_AVAILABLE:
        available['qwen'] = PROVIDERS['qwen']
    return available


def get_ai_client(provider, model=None):
    """Get AI client for a provider. Returns (client, model_name, provider) or (None, None, None)."""
    prov_config = PROVIDERS.get(provider)
    if not prov_config:
        return None, None, None

    # Validate model choice, fall back to default
    valid_models = prov_config['models']
    if not model or model not in valid_models:
        model = prov_config['default']

    if provider == 'anthropic':
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            return None, None, None
        return Anthropic(api_key=api_key), model, 'anthropic'

    elif provider == 'openai':
        if not OPENAI_AVAILABLE:
            return None, None, None
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None, None, None
        return OpenAI(api_key=api_key), model, 'openai'

    elif provider == 'qwen':
        if not OPENAI_AVAILABLE:
            return None, None, None
        api_key = os.getenv('QWEN_API_KEY')
        if not api_key:
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


def make_ai_api_call(client, model_name, provider, system_prompt, messages_content, max_tokens=16384):
    """Unified API call across providers."""
    if provider == 'anthropic':
        message = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": messages_content}],
            system=system_prompt
        )
        return message.content[0].text

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
    fence_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
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


def mark_script(provider, question_paper_pages, answer_key_pages, script_pages,
                subject='', rubrics_pages=None, review_instructions='', marking_instructions='',
                model=None, assign_type='short_answer', scoring_mode='status', total_marks=''):
    """
    Mark a student script using AI vision.

    Args:
        question_paper_pages: List of file bytes (each is a PDF or image)
        answer_key_pages: List of file bytes
        script_pages: List of file bytes
        rubrics_pages: Optional list of file bytes
        assign_type: 'short_answer' or 'rubrics'
        scoring_mode: 'status' (correct/partial/incorrect) or 'marks' (numerical)
        total_marks: Total marks for the assignment (when scoring_mode is 'marks')

    Returns dict with questions, overall_feedback, recommended_actions.
    """
    client, model_name, prov = get_ai_client(provider, model=model)
    if not client:
        return {'error': f'AI provider "{provider}" is not available (no API key configured)'}

    # Build system prompt
    rubrics_section = ""
    if rubrics_pages:
        rubrics_section = "\nGRADING RUBRICS have been provided — use them to evaluate subjective answers."

    review_section = ""
    if review_instructions.strip():
        review_section = f"\n\nREVIEW INSTRUCTIONS (follow these for how to write feedback):\n{review_instructions.strip()}"

    marking_section = ""
    if marking_instructions.strip():
        marking_section = f"\n\nMARKING INSTRUCTIONS (follow these for how to evaluate answers):\n{marking_instructions.strip()}"

    # Build scoring instructions based on mode
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

    type_context = ""
    if assign_type == 'rubrics':
        type_context = "\nThis is a RUBRICS-BASED assessment (essay / extended response). Use the provided rubrics as the primary evaluation criteria."

    system_prompt = f"""You are an experienced teacher marking a student's assignment script.

Subject: {subject or 'General'}
{type_context}
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

    # Build content array
    content = []

    def append_pages(label, pages):
        """Append one or more file pages to the content array."""
        content.append({"type": "text", "text": label})
        for i, page_bytes in enumerate(pages):
            content.append(build_content_block(page_bytes))
            if len(pages) > 1:
                content.append({"type": "text", "text": f"(Page {i + 1})"})

    # Question paper
    append_pages("QUESTION PAPER:", question_paper_pages)

    # Answer key
    append_pages("\nANSWER KEY (use for marking):", answer_key_pages)

    # Rubrics (optional)
    if rubrics_pages:
        append_pages("\nGRADING RUBRICS:", rubrics_pages)

    # Student script
    append_pages("\nSTUDENT SCRIPT (evaluate this):", script_pages)

    content.append({"type": "text", "text": "\nAnalyze this submission and provide JSON feedback:"})

    try:
        response_text = make_ai_api_call(
            client=client,
            model_name=model_name,
            provider=prov,
            system_prompt=system_prompt,
            messages_content=content,
            max_tokens=16384
        )

        result = parse_ai_response(response_text)
        result['generated_at'] = datetime.utcnow().isoformat()
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
