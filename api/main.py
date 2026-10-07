import csv
import io
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import psycopg
from fastapi import FastAPI, File, HTTPException, UploadFile
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/products"
)
EXPECTED_COLUMNS = ["sku", "name", "price", "category", "stock"]


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Création de la table au démarrage si elle n'existe pas
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                sku        TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                price      NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
                category   TEXT,
                stock      INTEGER NOT NULL CHECK (stock >= 0),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    yield


app = FastAPI(title="Product Data Hub API", lifespan=lifespan)


def validate_row(row: list[str]) -> tuple[dict | None, list[str]]:
    """Valide une ligne CSV et retourne (produit, erreurs)."""
    if len(row) != len(EXPECTED_COLUMNS):
        return None, [f"{len(row)} colonne(s) au lieu de {len(EXPECTED_COLUMNS)}"]

    sku, name, price, category, stock = (value.strip() for value in row)
    errors = []

    if not sku:
        errors.append("sku manquant")
    if not name:
        errors.append("name manquant")

    parsed_price = None
    if not price:
        errors.append("price manquant")
    else:
        try:
            parsed_price = Decimal(price)
            if parsed_price < 0:
                errors.append("price négatif")
        except InvalidOperation:
            errors.append(f"price invalide : {price!r}")

    parsed_stock = None
    if not stock:
        errors.append("stock manquant")
    else:
        try:
            parsed_stock = int(stock)
            if parsed_stock < 0:
                errors.append("stock négatif")
        except ValueError:
            errors.append(f"stock invalide : {stock!r}")

    if errors:
        return None, errors

    return {
        "sku": sku,
        "name": name,
        "price": parsed_price,
        "category": category or None,
        "stock": parsed_stock,
    }, []


@app.get("/health")
def health():
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="Base de données injoignable")
    return {"status": "ok"}


@app.post("/products/import")
async def import_products(file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))

    header = [column.strip().lower() for column in next(reader, [])]
    if header != EXPECTED_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"En-tête attendu : {','.join(EXPECTED_COLUMNS)}",
        )

    valid, rejected, seen_skus = [], [], set()
    # La ligne 1 est l'en-tête
    for line_number, row in enumerate(reader, start=2):
        if not any(value.strip() for value in row):
            continue

        product, errors = validate_row(row)
        if product and product["sku"] in seen_skus:
            product, errors = None, [f"sku en doublon : {product['sku']}"]

        if errors:
            rejected.append({"line": line_number, "raw": ",".join(row), "errors": errors})
            continue

        seen_skus.add(product["sku"])
        valid.append(product)

    # Upsert : un sku existant est mis à jour
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO products (sku, name, price, category, stock)
                VALUES (%(sku)s, %(name)s, %(price)s, %(category)s, %(stock)s)
                ON CONFLICT (sku) DO UPDATE SET
                    name = EXCLUDED.name,
                    price = EXCLUDED.price,
                    category = EXCLUDED.category,
                    stock = EXCLUDED.stock,
                    updated_at = now()
                """,
                valid,
            )

    return {"imported": len(valid), "rejected": len(rejected), "errors": rejected}


@app.get("/products")
def list_products(category: str | None = None):
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM products WHERE category = %s ORDER BY sku", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
    return rows


@app.get("/products/{sku}")
def get_product(sku: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE sku = %s", (sku,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Produit {sku} introuvable")
    return row
