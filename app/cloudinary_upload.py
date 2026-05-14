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
