from io import BytesIO

from pypdf import PdfReader

from app.errors import DomainError

MAX_PDF_BYTES = 5 * 1024 * 1024


def validate_pdf(content: bytes) -> None:
    if not content.startswith(b"%PDF-") or len(content) > MAX_PDF_BYTES:
        raise DomainError("Document PDF invalide ou supérieur à 5 Mo.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DomainError("Déposez un PDF sans mot de passe.")
        if not 1 <= len(reader.pages) <= 200:
            raise DomainError("Le PDF doit contenir entre 1 et 200 pages.")
    except DomainError:
        raise
    except Exception:
        raise DomainError("Le fichier ne peut pas être lu comme un PDF valide.") from None
