import uuid
from fastapi import APIRouter, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user
from app import models
from app.cloudinary_upload import upload_file

router = APIRouter()

@router.post("/purchases/{purchase_id}/documents")
async def upload_document(
    purchase_id: int,
    doc_type: str = Form(...),  # remito | factura | otro
    invoice_number: str = Form(""),
    invoice_date: str = Form(""),
    invoice_amount: str = Form(""),
    remito_date: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    purchase = db.query(models.Purchase).filter(models.Purchase.id == purchase_id).first()
    if not purchase:
        raise HTTPException(status_code=404)

    contents = await file.read()
    unique_name = f"{uuid.uuid4()}_{file.filename}"
    result = upload_file(contents, unique_name)

    amount = None
    if invoice_amount.strip():
        try:
            amount = float(invoice_amount.replace(",", "."))
        except ValueError:
            pass

    doc = models.Document(
        purchase_id=purchase_id,
        doc_type=doc_type,
        file_url=result["url"],
        filename=file.filename,
        invoice_number=invoice_number or None,
        invoice_date=invoice_date or None,
        invoice_amount=amount,
        remito_date=remito_date or None,
        uploaded_by_id=current_user.id
    )
    db.add(doc)

    # Verificar alerta de monto
    if doc_type == "factura" and amount and purchase.estimated_amount:
        if float(amount) > float(purchase.estimated_amount) * 1.10:
            purchase.amount_alert = True

    db.commit()
    return RedirectResponse(url=f"/purchases/{purchase_id}", status_code=303)
