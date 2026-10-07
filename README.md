# product-data-hub
Tutorial project on product data hub

## Lancer le projet

1. Prérequis : Docker Desktop démarré.
2. À la racine du dépôt : `docker compose up -d --build` (lance PostgreSQL puis l'API).
3. L'API répond sur http://localhost:8000 — documentation interactive sur http://localhost:8000/docs.
4. Importer un CSV : `curl -F file=@samples/products.csv localhost:8000/products/import`, puis consulter `GET /imports`.
5. Arrêter : `docker compose down` (ajouter `-v` pour supprimer aussi les données).
