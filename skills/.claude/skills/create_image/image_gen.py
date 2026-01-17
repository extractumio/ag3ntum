#!/usr/bin/env python3
"""
Unified image generation module supporting multiple vendors (Google Gemini, OpenAI).

This module provides both CLI and programmatic interface for generating and editing
images using various AI image models.

Usage:
    CLI: python image_gen.py "prompt" --vendor google -o image.png
         python image_gen.py "prompt" --vendor openai --hd -o image.png
         python image_gen.py "prompt" --reference ref.jpg -o edited.png
    Code: from image_gen import generate_image, edit_image
          result = generate_image("prompt", vendor="google")
          result = generate_image("prompt", vendor="openai", hd=True)
          result = edit_image("edit prompt", "photo.jpg", vendor="google")
"""

import argparse
import base64
import io
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from PIL import Image as PILImage
from google.genai.models import types

def _find_project_dotenv(start_path: Path) -> Path | None:
    """
    Find the nearest `.env` file by walking up parent directories.

    We intentionally search from a *file location* (not CWD) so running scripts
    from subfolders still loads the project-root `.env`.
    """
    current: Path = start_path.resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        candidate: Path = parent / ".env"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_project_dotenv() -> None:
    """
    Load `.env` from the project root (or nearest parent), without depending on CWD.

    - Does **not** override already-exported environment variables.
    - If `.env` is absent, we proceed; downstream code may still fail fast if keys are missing.
    """
    dotenv_path: Path | None = _find_project_dotenv(Path(__file__))
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        # Fallback to default behavior (CWD/parents) just in case.
        load_dotenv(override=False)


# Load `.env` deterministically from project root (or nearest parent) at import time
_load_project_dotenv()


# Type definitions
Vendor = Literal["google", "openai"]
SUPPORTED_VENDORS: list[str] = ["google", "openai"]

# Common aspect ratio to size mapping for OpenAI
ASPECT_RATIO_TO_SIZE: dict[str, str] = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "4:3": "1536x1024",
    "3:4": "1024x1536",
    "21:9": "1536x1024",
}

# Supported aspect ratios (unified)
SUPPORTED_ASPECT_RATIOS: list[str] = [
    "1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"
]


def _save_image(image: PILImage.Image, output_path: str) -> str:
    """Save a PIL image to disk and return the path."""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(output_file))
    return str(output_file)


def _get_mime_type(file_path: Path) -> str:
    """Get MIME type from file extension."""
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return mime_types.get(file_path.suffix.lower(), "image/jpeg")


class ImageProvider(ABC):
    """Abstract base class for image generation providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        aspect_ratio: str,
        high_quality: bool,
        model: str | None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Generate an image from a text prompt."""
        pass

    @abstractmethod
    def edit(
        self,
        prompt: str,
        reference_image: Path,
        high_quality: bool,
        model: str | None,
        mask_image: Path | None = None,
        seed: int | None = None,
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing image based on a prompt."""
        pass


class GoogleProvider(ImageProvider):
    """Google Gemini image generation provider."""

    MODEL_DRAFT: str = "gemini-2.5-flash-image"
    MODEL_PRO: str = "gemini-3-pro-image-preview"

    def __init__(self, api_key: str):
        from google import genai
        self.genai = genai
        self.client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt: str,
        aspect_ratio: str,
        high_quality: bool,
        model: str | None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        from google.genai.types import GenerateContentConfig, ImageConfig

        used_model = model or (self.MODEL_PRO if high_quality else self.MODEL_DRAFT)

        result: dict[str, Any] = {
            "success": False,
            "error": None,
            "image": None,
            "text": None,
            "model": used_model,
            "vendor": "google",
            "seed": seed,
        }

        try:
            img_config = ImageConfig(aspect_ratio=aspect_ratio)
            config = GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=img_config,
                seed=seed,
            )

            response = self.client.models.generate_content(
                model=used_model,
                contents=[prompt],
                config=config,
            )

            text_parts: list[str] = []
            for part in response.parts:
                if part.text is not None:
                    text_parts.append(part.text)
                elif part.inline_data is not None and result["image"] is None:
                    result["image"] = part.as_image()
                    result["success"] = True

            if text_parts:
                result["text"] = "\n".join(text_parts)

            if result["image"] is None and not result["error"]:
                result["error"] = "No image was generated in the response."

        except Exception as e:
            result["error"] = f"API request failed: {str(e)}"

        return result

    def edit(
        self,
        prompt: str,
        reference_image: Path,
        high_quality: bool,
        model: str | None,
        mask_image: Path | None = None,
        seed: int | None = None,
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        from google.genai.types import GenerateContentConfig, Part

        used_model = model or (self.MODEL_PRO if high_quality else self.MODEL_DRAFT)

        result: dict[str, Any] = {
            "success": False,
            "error": None,
            "image": None,
            "text": None,
            "model": used_model,
            "vendor": "google",
            "seed": seed,
        }

        try:
            with open(reference_image, "rb") as f:
                image_bytes = f.read()

            mime_type = _get_mime_type(reference_image)
            image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)

            config = GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                seed=seed,
                system_instruction=system_instruction + "\n\n" + prompt,
                image_config=types.ImageConfig(
                    image_size="1K",
                ),
            )

            response = self.client.models.generate_content(
                model=used_model,
                contents=[image_part, "Edit the image"],
                config=config,
            )

            text_parts: list[str] = []
            for part in response.parts:
                if part.text is not None:
                    text_parts.append(part.text)
                elif part.inline_data is not None and result["image"] is None:
                    result["image"] = part.as_image()
                    result["success"] = True

            if text_parts:
                result["text"] = "\n".join(text_parts)

            if result["image"] is None and not result["error"]:
                result["error"] = "No image was generated in the response."

        except Exception as e:
            result["error"] = f"API request failed: {str(e)}"

        return result


class OpenAIProvider(ImageProvider):
    """OpenAI image generation provider."""

    MODEL_IMAGE: str = "gpt-image-1"

    def __init__(self, api_key: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)

    def generate(
        self,
        prompt: str,
        aspect_ratio: str,
        high_quality: bool,
        model: str | None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        used_model = model or self.MODEL_IMAGE
        size = ASPECT_RATIO_TO_SIZE.get(aspect_ratio, "1024x1024")
        # gpt-image-1 uses: 'low', 'medium', 'high', 'auto'
        quality = "high" if high_quality else "low"

        result: dict[str, Any] = {
            "success": False,
            "error": None,
            "image": None,
            "text": None,
            "model": used_model,
            "vendor": "openai",
            "seed": seed,
        }

        try:
            # gpt-image-1 returns b64_json by default (no response_format param needed)
            # Note: OpenAI doesn't support seed for image generation
            response = self.client.images.generate(
                model=used_model,
                prompt=prompt,
                n=1,
                size=size,
                quality=quality,
            )

            image_b64 = response.data[0].b64_json
            if image_b64:
                image_bytes = base64.b64decode(image_b64)
                result["image"] = PILImage.open(io.BytesIO(image_bytes))
                result["success"] = True
            else:
                result["error"] = "No image data in response."

        except Exception as e:
            result["error"] = f"API request failed: {str(e)}"

        return result

    def edit(
        self,
        prompt: str,
        reference_image: Path,
        high_quality: bool,
        model: str | None,
        mask_image: Path | None = None,
        seed: int | None = None,
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        used_model = model or self.MODEL_IMAGE

        result: dict[str, Any] = {
            "success": False,
            "error": None,
            "image": None,
            "text": None,
            "model": used_model,
            "vendor": "openai",
            "seed": seed,
        }

        # For OpenAI, prepend system instruction to prompt if provided
        effective_prompt = prompt
        if system_instruction:
            effective_prompt = f"{system_instruction}\n\n{prompt}"

        try:
            with open(reference_image, "rb") as img_f:
                edit_params: dict[str, Any] = {
                    "model": used_model,
                    "image": img_f,
                    "prompt": effective_prompt,
                    "n": 1,
                }

                if mask_image:
                    with open(mask_image, "rb") as mask_f:
                        edit_params["mask"] = mask_f
                        response = self.client.images.edit(**edit_params)
                else:
                    response = self.client.images.edit(**edit_params)

            image_b64 = response.data[0].b64_json
            if image_b64:
                image_bytes = base64.b64decode(image_b64)
                result["image"] = PILImage.open(io.BytesIO(image_bytes))
                result["success"] = True
            else:
                result["error"] = "No image data in response."

        except Exception as e:
            result["error"] = f"API request failed: {str(e)}"

        return result


def _get_provider(vendor: Vendor, api_key: str | None = None) -> ImageProvider:
    """Get the appropriate provider based on vendor name."""
    if vendor == "google":
        key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise ValueError(
                "Google API key not provided. Set GEMINI_API_KEY environment variable "
                "or pass api_key parameter."
            )
        return GoogleProvider(api_key=key)
    elif vendor == "openai":
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError(
                "OpenAI API key not provided. Set OPENAI_API_KEY environment variable "
                "or pass api_key parameter."
            )
        return OpenAIProvider(api_key=key)
    else:
        raise ValueError(f"Unknown vendor: {vendor}. Supported: {SUPPORTED_VENDORS}")


def generate_image(
    prompt: str,
    output_path: str | None = None,
    aspect_ratio: str = "1:1",
    vendor: Vendor = "google",
    high_quality: bool = False,
    model: str | None = None,
    api_key: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """
    Generate an image using the specified vendor's API.

    Args:
        prompt: Text description of the image to generate.
        output_path: Optional path to save the generated image.
        aspect_ratio: Aspect ratio of the image. Supported: "1:1", "2:3", "3:2",
                      "3:4", "4:3", "9:16", "16:9", "21:9". Default: "1:1".
        vendor: Which vendor to use ("google" or "openai"). Default: "google".
        high_quality: If True, use higher quality model/settings. Default: False.
        model: Model name to override default. If None, uses vendor's default.
        api_key: API key. If None, uses environment variable for the vendor.
        seed: Random seed for reproducibility (Google only). Default: None.

    Returns:
        Dictionary with:
            - success (bool): Whether generation was successful.
            - image_path (str | None): Path where image was saved.
            - text (str | None): Text response if any (Google only).
            - error (str | None): Error message if failed.
            - image (PIL.Image.Image | None): PIL Image object.
            - model (str): The model that was used.
            - vendor (str): The vendor that was used.
            - seed (int | None): The seed that was used.

    Raises:
        ValueError: If invalid aspect_ratio, vendor, or missing API key.
    """
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(
            f"Invalid aspect_ratio '{aspect_ratio}'. "
            f"Supported: {SUPPORTED_ASPECT_RATIOS}"
        )

    provider = _get_provider(vendor, api_key)
    result = provider.generate(prompt, aspect_ratio, high_quality, model, seed)

    # Add common fields
    result["image_path"] = None

    # Save image if output path provided and generation succeeded
    if output_path and result["success"] and result["image"]:
        try:
            result["image_path"] = _save_image(result["image"], output_path)
        except Exception as e:
            result["error"] = f"Failed to save image: {str(e)}"

    return result


def edit_image(
    prompt: str,
    reference_image: str,
    output_path: str | None = None,
    vendor: Vendor = "google",
    high_quality: bool = False,
    model: str | None = None,
    mask_image: str | None = None,
    api_key: str | None = None,
    seed: int | None = None,
    system_instruction: str | None = None,
) -> dict[str, Any]:
    """
    Edit an existing image based on a text prompt.

    Args:
        prompt: Text description of the edit to apply (user prompt).
        reference_image: Path to the reference image file to edit.
        output_path: Optional path to save the edited image.
        vendor: Which vendor to use ("google" or "openai"). Default: "google".
        high_quality: If True, use higher quality model/settings. Default: False.
        model: Model name to override default. If None, uses vendor's default.
        mask_image: Optional path to mask image (OpenAI only). Transparent areas
                   indicate where to edit.
        api_key: API key. If None, uses environment variable for the vendor.
        seed: Random seed for reproducibility (Google only). Default: None.
        system_instruction: System-level instruction for the model (e.g., style guide).
                           For Google, this is passed as system_instruction config.
                           For OpenAI, this is prepended to the prompt. Default: None.

    Returns:
        Dictionary with:
            - success (bool): Whether edit was successful.
            - image_path (str | None): Path where image was saved.
            - text (str | None): Text response if any (Google only).
            - error (str | None): Error message if failed.
            - image (PIL.Image.Image | None): Edited PIL Image object.
            - model (str): The model that was used.
            - vendor (str): The vendor that was used.
            - seed (int | None): The seed that was used.

    Raises:
        ValueError: If invalid vendor or missing API key.
        FileNotFoundError: If reference image or mask file does not exist.
    """
    ref_path = Path(reference_image)
    if not ref_path.exists():
        raise FileNotFoundError(f"Reference image not found: {reference_image}")

    mask_path: Path | None = None
    if mask_image:
        mask_path = Path(mask_image)
        if not mask_path.exists():
            raise FileNotFoundError(f"Mask image not found: {mask_image}")

    provider = _get_provider(vendor, api_key)
    result = provider.edit(prompt, ref_path, high_quality, model, mask_path, seed, system_instruction)

    # Add common fields
    result["image_path"] = None

    # Save image if output path provided and edit succeeded
    if output_path and result["success"] and result["image"]:
        try:
            result["image_path"] = _save_image(result["image"], output_path)
        except Exception as e:
            result["error"] = f"Failed to save image: {str(e)}"

    return result


def _resolve_prompt(prompt_arg: str | None, prompt_file: str | None) -> str:
    """Resolve the prompt from either direct argument or file."""
    if prompt_file:
        file_path = Path(prompt_file)
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
        prompt = file_path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError(f"Prompt file is empty: {prompt_file}")
        return prompt
    elif prompt_arg:
        return prompt_arg
    else:
        raise ValueError("No prompt provided. Use positional argument or --prompt-file.")


def main() -> int:
    """CLI entry point for image generation."""
    parser = argparse.ArgumentParser(
        description="Generate or edit images using AI (Google Gemini or OpenAI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Text-to-image generation (inline prompt)
  python image_gen.py "A sunset over mountains" -o sunset.png
  python image_gen.py "A cat in a hat" --vendor openai -o cat.png
  python image_gen.py "High quality portrait" --hq -o portrait.png

  # Text-to-image generation (prompt from file)
  python image_gen.py -p prompt.txt -o sunset.png
  python image_gen.py --prompt-file detailed_prompt.md --hq -o portrait.png

  # Image editing
  python image_gen.py "Make it cyberpunk" --reference photo.jpg -o edited.png
  python image_gen.py -p edit_instructions.txt --reference photo.jpg -o edited.png
  python image_gen.py "Add a hat" --vendor openai --reference person.png --mask mask.png -o hat.png

Vendors:
  google (default) - Google Gemini (gemini-2.5-flash-image / gemini-3-pro-image-preview)
  openai           - OpenAI (gpt-image-1)

Quality:
  Default: Fast/standard quality
  --hq:    High quality (Google pro model / OpenAI HD)

Prompt Sources:
  Positional arg   - Direct text: python image_gen.py "your prompt here"
  -p/--prompt-file - From file:   python image_gen.py -p prompt.txt

Aspect Ratios: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9
        """,
    )

    parser.add_argument(
        "prompt",
        type=str,
        nargs="?",
        default=None,
        help="Text prompt (inline). Alternative: use --prompt-file for long prompts.",
    )

    parser.add_argument(
        "-p", "--prompt-file",
        type=str,
        default=None,
        help="Path to file containing the prompt. Supports .txt, .md, or any text file.",
    )

    parser.add_argument(
        "-o", "--output",
        type=str,
        default="generated_image.png",
        help="Output file path. Default: generated_image.png",
    )

    parser.add_argument(
        "-r", "--aspect-ratio",
        type=str,
        default="1:1",
        choices=SUPPORTED_ASPECT_RATIOS,
        help="Aspect ratio of the image. Default: 1:1",
    )

    parser.add_argument(
        "--vendor",
        type=str,
        default="google",
        choices=SUPPORTED_VENDORS,
        help="Which vendor to use. Default: google",
    )

    parser.add_argument(
        "--hq",
        action="store_true",
        help="Use high quality mode (Google pro model / OpenAI HD).",
    )

    parser.add_argument(
        "-m", "--model",
        type=str,
        default=None,
        help="Override the default model for the vendor.",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key. If not provided, uses environment variable for the vendor.",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output.",
    )

    # Image editing options
    parser.add_argument(
        "--reference",
        type=str,
        default=None,
        help="Path to reference image for editing. Enables edit mode.",
    )

    parser.add_argument(
        "--mask",
        type=str,
        default=None,
        help="Path to mask image (OpenAI only). Transparent areas indicate where to edit.",
    )

    args = parser.parse_args()

    # Resolve prompt from argument or file
    try:
        prompt = _resolve_prompt(args.prompt, args.prompt_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    # Determine effective model name for display
    if args.model:
        effective_model = args.model
    elif args.vendor == "google":
        effective_model = GoogleProvider.MODEL_PRO if args.hq else GoogleProvider.MODEL_DRAFT
    else:
        effective_model = OpenAIProvider.MODEL_IMAGE

    # Check if we're in edit mode
    if args.reference:
        # Image editing mode
        if args.verbose:
            print(f"Vendor: {args.vendor}")
            print(f"Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
            if args.prompt_file:
                print(f"Prompt file: {args.prompt_file}")
            print(f"Reference: {args.reference}")
            if args.mask:
                print(f"Mask: {args.mask}")
            print(f"Output: {args.output}")
            print(f"Model: {effective_model}")
            print(f"Quality: {'HIGH' if args.hq else 'STANDARD'}")
            print("Editing image...")

        try:
            result = edit_image(
                prompt=prompt,
                reference_image=args.reference,
                output_path=args.output,
                vendor=args.vendor,
                high_quality=args.hq,
                model=args.model,
                mask_image=args.mask,
                api_key=args.api_key,
            )
        except (ValueError, FileNotFoundError) as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

        if result["success"]:
            print("✓ Image edited successfully!")
            if result["image_path"]:
                print(f"  Saved to: {result['image_path']}")
            print(f"  Vendor: {result['vendor']}")
            print(f"  Model: {result['model']}")
            if result.get("text"):
                print(f"  Text: {result['text']}")
            return 0
        else:
            print(f"✗ Image editing failed: {result['error']}", file=sys.stderr)
            return 1
    else:
        # Text-to-image generation mode
        if args.verbose:
            print(f"Vendor: {args.vendor}")
            print(f"Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
            if args.prompt_file:
                print(f"Prompt file: {args.prompt_file}")
            print(f"Output: {args.output}")
            print(f"Aspect Ratio: {args.aspect_ratio}")
            print(f"Model: {effective_model}")
            print(f"Quality: {'HIGH' if args.hq else 'STANDARD'}")
            print("Generating image...")

        try:
            result = generate_image(
                prompt=prompt,
                output_path=args.output,
                aspect_ratio=args.aspect_ratio,
                vendor=args.vendor,
                high_quality=args.hq,
                model=args.model,
                api_key=args.api_key,
            )
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

        if result["success"]:
            print("✓ Image generated successfully!")
            if result["image_path"]:
                print(f"  Saved to: {result['image_path']}")
            print(f"  Vendor: {result['vendor']}")
            print(f"  Model: {result['model']}")
            if result.get("text"):
                print(f"  Text: {result['text']}")
            return 0
        else:
            print(f"✗ Image generation failed: {result['error']}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
