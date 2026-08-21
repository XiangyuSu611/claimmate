from .base import *

def run_ocr_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=OCR_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def extract_image_text(path: Path) -> str:
    """Extract image text locally, preferring the native Windows OCR engine."""
    try:
        if path.stat().st_size > OCR_MAX_BYTES:
            return ""
    except OSError:
        return ""

    if sys.platform == "win32":
        helper = Path(__file__).with_name("windows_ocr.ps1")
        powershell = shutil.which("powershell.exe")
        if helper.is_file() and powershell:
            text = run_ocr_command(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    str(path.resolve()),
                ]
            )
            if text:
                return text

    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    for languages in ("chi_sim+eng", None):
        command = [tesseract, str(path.resolve()), "stdout", "--psm", "6"]
        if languages:
            command.extend(["-l", languages])
        text = run_ocr_command(command)
        if text:
            return text
    return ""


def clean_ocr_text(value: str) -> str:
    """Normalize spacing and punctuation artifacts common in local OCR output."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"(?<=\d)\s*[一—–−]\s*(?=\d)", "-", value)
    value = re.sub(
        r"(\d{4}-\d{1,2}-\d{1,2})\s+(?=\d{1,2}\s*[:：])",
        r"\1T",
        value,
    )
    value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
    value = re.sub(r"(?<=[a-zA-Z0-9])\s+(?=[a-zA-Z0-9])", "", value)
    value = re.sub(r"\s*([{}\[\]():：,，._|/\\-])\s*", r"\1", value)
    return value


def extract_text(path: Path, original_name: str | None = None) -> str:
    pieces = [Path(original_name).stem if original_name else path.stem]
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".csv", ".json"}:
            pieces.append(path.read_text(encoding="utf-8", errors="ignore")[:100000])
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError:
                try:
                    from PyPDF2 import PdfReader  # type: ignore
                except ImportError:
                    PdfReader = None  # type: ignore
            if PdfReader:
                reader = PdfReader(str(path))
                pieces.extend((page.extract_text() or "") for page in reader.pages[:10])
        elif suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
                pieces.append(re.sub(r"<[^>]+>", " ", xml)[:100000])
        elif suffix in IMAGE_EXTENSIONS:
            pieces.append(clean_ocr_text(extract_image_text(path))[:100000])
    except Exception:
        # Preserve the filename context for the model and retry extraction later.
        pass
    return normalize("\n".join(pieces))


def analyze(path: Path, config: dict[str, Any], original_name: str | None = None) -> dict[str, Any]:
    original_name = original_name or path.name
    text = extract_text(path, original_name)
    return {
        "source": path,
        "sha256": sha256(path),
        "original_name": original_name,
        "role": None,
        "role_confidence": "none",
        "category": None,
        "category_confidence": "none",
        "amount": None,
        "merchant": None,
        "date_tokens": [],
        "reference_tokens": [],
        "routing_text": text,
    }
