import os
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    secure=True
)

def upload_file(file_bytes: bytes, filename: str, folder: str = "mtr-compras") -> dict:
    result = cloudinary.uploader.upload(
        file_bytes,
        public_id=f"{folder}/{filename}",
        resource_type="auto",
        use_filename=True,
        unique_filename=True,
    )
    return {"url": result["secure_url"], "public_id": result["public_id"]}


def delete_file(public_id: str) -> None:
    """Borra un archivo de Cloudinary dado su public_id."""
    cloudinary.uploader.destroy(public_id)
async def upload_factura_file(file) -> dict:
    if not file or not file.filename:
        return None

    contents = await file.read()
    if not contents:
        return None

    max_size = 10 * 1024 * 1024
    if len(contents) > max_size:
        raise ValueError("El archivo supera los 10MB.")

    filename_lower = file.filename.lower()
    if not filename_lower.endswith((".pdf", ".jpg", ".jpeg", ".png")):
        raise ValueError("Solo se permiten PDF, JPG o PNG.")

    result = upload_file(contents, file.filename, folder="facturas")

    return {
        "url": result["url"],
        "public_id": result["public_id"],
        "filename": file.filename,
    }


def delete_factura_file(public_id: str) -> bool:
    if not public_id:
        return False

    try:
        delete_file(public_id)
        return True
    except Exception:
        return False

