---
name: create-image
description: |
  Generate images from text prompts or edit existing images using AI. Supports 
  multiple vendors (Google Gemini, OpenAI) with a unified interface. Configurable 
  aspect ratios, quality tiers, and optional mask-based editing. Prompts can be 
  provided inline or from external files. Returns a PIL Image object and optionally 
  saves to disk. Requires GOOGLE_API_KEY or OPENAI_API_KEY environment variable.
---

# Create Image

## Overview

This skill generates images from natural language descriptions or edits existing images using AI image generation models. It provides a **unified interface** supporting multiple vendors:

- **Google Gemini** (default) - Fast drafts and high-quality pro output
- **OpenAI** - HD quality with optional mask-based editing

## Quick Start

```bash
# Generate an image (Google, default)
python image_gen.py "A sunset over mountains" -o sunset.png

# Generate with prompt from file
python image_gen.py -p prompt.txt -o sunset.png

# Generate with OpenAI
python image_gen.py "A sunset over mountains" --vendor openai -o sunset.png

# High quality
python image_gen.py "Detailed portrait" --hq -o portrait.png

# Edit an existing image
python image_gen.py "Make the shirt green" --reference photo.jpg -o edited.png
```

---

## Vendors

| Vendor | Flag | Models | Best For |
|--------|------|--------|----------|
| Google Gemini | `--vendor google` (default) | gemini-2.5-flash-image (default), gemini-3-pro-image-preview (--hq) | Fast iterations, aspect ratio control |
| OpenAI | `--vendor openai` | gpt-image-1 | HD quality, mask-based targeted edits |

### API Keys

| Vendor | Environment Variable |
|--------|---------------------|
| Google | `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |

---

## CLI Reference

### Basic Usage

```bash
# Inline prompt
python image_gen.py "<prompt>" [options]

# Prompt from file
python image_gen.py -p <prompt-file> [options]
```

### Options

| Flag | Long Form | Default | Description |
|------|-----------|---------|-------------|
| | (positional) | None | Inline text prompt |
| `-p` | `--prompt-file` | None | Path to file containing prompt (.txt, .md, etc.) |
| `-o` | `--output` | `generated_image.png` | Output file path |
| `-r` | `--aspect-ratio` | `1:1` | Aspect ratio (1:1, 16:9, 9:16, etc.) |
| | `--vendor` | `google` | Vendor: `google` or `openai` |
| | `--hq` | off | High quality mode |
| `-m` | `--model` | (vendor default) | Override model |
| `-v` | `--verbose` | off | Verbose output |
| | `--api-key` | (from env) | API key override |
| | `--reference` | None | Reference image (enables edit mode) |
| | `--mask` | None | Mask image (OpenAI only) |

### Prompt Sources

You can provide the prompt in two ways:

1. **Inline (positional argument):** `python image_gen.py "your prompt here"`
2. **From file:** `python image_gen.py -p prompt.txt`

The file option is useful for:
- Long, detailed prompts
- Reusable prompt templates
- Multi-line prompts with formatting

### Supported Aspect Ratios

`1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9`, `21:9`

---

## Programmatic API

### generate_image()

```python
from image_gen import generate_image

result = generate_image(
    prompt="A sunset over mountains",
    output_path="sunset.png",      # optional
    aspect_ratio="16:9",           # default: "1:1"
    vendor="google",               # or "openai"
    high_quality=False,            # True for pro/HD
    model=None,                    # override default model
    api_key=None,                  # override env variable
)
```

### edit_image()

```python
from image_gen import edit_image

result = edit_image(
    prompt="Make the shirt green",
    reference_image="photo.jpg",
    output_path="edited.png",      # optional
    vendor="google",               # or "openai"
    high_quality=False,            # True for pro/HD
    model=None,                    # override default model
    mask_image=None,               # OpenAI only: path to mask
    api_key=None,                  # override env variable
)
```

### Return Value

Both functions return the same dictionary:

```python
{
    "success": bool,           # True if successful
    "image_path": str | None,  # Path where image was saved
    "text": str | None,        # Text response (Google only)
    "error": str | None,       # Error message if failed
    "image": PIL.Image.Image | None,  # PIL Image object
    "model": str,              # Model that was used
    "vendor": str,             # Vendor that was used
}
```

---

## Instructions for Claude

### Step 1: Determine Parameters

From the user's request, extract:
- **Prompt**: The image description or edit instruction (required)
  - For long prompts, save to a file and use `-p`
- **Vendor**: User preference, or default to `google`
- **Mode**: Generate (no reference) or Edit (reference provided)
- **Quality**: Standard (default) or high (`--hq`)
- **Aspect ratio**: Based on intended use (default: `1:1`)
- **Output path**: Where to save the image

### Step 2: Choose Vendor

| Scenario | Recommended Vendor |
|----------|-------------------|
| Default / fast iterations | `google` |
| User has only OpenAI key | `openai` |
| Need mask-based targeted edits | `openai` |
| User explicitly requests | As specified |

### Step 3: Prepare Prompt

**For short prompts (< 200 chars):**
```bash
python image_gen.py "Short prompt here" -o output.png
```

**For long/detailed prompts:**
1. Save prompt to a file (e.g., `prompt.txt`)
2. Use the `-p` flag:
```bash
python image_gen.py -p prompt.txt -o output.png
```

### Step 4: Execute

**CLI with inline prompt:**
```bash
python image_gen.py "<prompt>" --vendor <vendor> -r <aspect-ratio> -o <output> [--hq]
```

**CLI with prompt file:**
```bash
python image_gen.py -p <prompt-file> --vendor <vendor> -r <aspect-ratio> -o <output> [--hq]
```

**CLI for editing:**
```bash
python image_gen.py "<prompt>" --reference <image> -o <output>
python image_gen.py -p <prompt-file> --reference <image> -o <output>
```

**Programmatic:**
```python
from image_gen import generate_image, edit_image

# Generation
result = generate_image(prompt="...", vendor="google", aspect_ratio="16:9")

# Editing
result = edit_image(prompt="...", reference_image="photo.jpg", vendor="google")
```

### Step 5: Handle Result

```python
if result["success"]:
    print(f"Saved to: {result['image_path']}")
    image = result["image"]  # PIL.Image.Image
else:
    print(f"Error: {result['error']}")
```

---

## Examples

### Text-to-Image Generation

```bash
# Inline prompt (short)
python image_gen.py "A cartoon cat wizard" -o wizard_cat.png

# Prompt from file (long/detailed)
python image_gen.py -p character_description.txt -o character.png

# Widescreen landscape
python image_gen.py "Mountain panorama at sunset" -r 16:9 -o mountains.png

# High quality with Google Pro
python image_gen.py -p detailed_prompt.md --hq -r 3:4 -o portrait.png

# Using OpenAI
python image_gen.py "Steampunk clockwork" --vendor openai -o steampunk.png

# OpenAI HD quality with prompt file
python image_gen.py -p prompt.txt --vendor openai --hq -o botanical.png
```

### Image Editing

```bash
# Edit with inline prompt
python image_gen.py "Make the background a beach" --reference portrait.jpg -o beach.png

# Edit with prompt from file
python image_gen.py -p edit_instructions.txt --reference portrait.jpg -o edited.png

# Edit with Google Pro
python image_gen.py "Change shirt to green" --reference person.jpg --hq -o green.png

# Edit with OpenAI (whole image variation)
python image_gen.py "Make it look like watercolor" --vendor openai --reference photo.png -o watercolor.png

# Targeted edit with OpenAI mask
python image_gen.py -p bg_replacement.txt --vendor openai --reference portrait.png --mask bg_mask.png -o space.png
```

### Programmatic Examples

```python
from image_gen import generate_image, edit_image
from pathlib import Path

# Generate with inline prompt
result = generate_image(
    prompt="A serene Japanese garden",
    aspect_ratio="16:9",
    high_quality=True,
)
if result["success"]:
    result["image"].show()

# Generate with prompt from file (read file yourself)
prompt = Path("detailed_prompt.txt").read_text()
result = generate_image(
    prompt=prompt,
    output_path="detailed_image.png",
    high_quality=True,
)

# Edit an image
result = edit_image(
    prompt="Add dramatic sunset lighting",
    reference_image="landscape.jpg",
    output_path="dramatic_landscape.png",
    vendor="google",
    high_quality=True,
)
if result["success"]:
    print(f"Saved to {result['image_path']}")

# OpenAI with mask
result = edit_image(
    prompt="Replace the sky with northern lights",
    reference_image="photo.png",
    mask_image="sky_mask.png",
    vendor="openai",
    output_path="aurora.png",
)
```

---

## Mask Image Guidelines (OpenAI only)

For targeted image editing with OpenAI:
- Use **PNG format**
- Same dimensions as reference image
- **Transparent areas**: Where the model may change pixels
- **Opaque areas**: Where the original must be preserved

---

## Error Handling

| Error Type | Cause |
|------------|-------|
| `ValueError` | Invalid aspect ratio, vendor, missing API key, or empty prompt |
| `FileNotFoundError` | Prompt file, reference, or mask image doesn't exist |
| `result["error"]` | API/network errors (check `result["success"]`) |
