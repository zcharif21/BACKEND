import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient, TEXT
from dotenv import load_dotenv


def create_app() -> Flask:
    load_dotenv()
    app = Flask(__name__)

    CORS(app, resources={r"/*": {"origins": os.getenv("CORS_ORIGINS", "*")}})

    mongo_uri = os.getenv("MONGO_URI")
    mongo_db = os.getenv("MONGO_DB")
    mongo_collection = os.getenv("MONGO_COLLECTION")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[mongo_db]
    collection = db[mongo_collection]

    # Indexes pour accélérer filtres/recherches
    try:
        collection.create_index([("year", 1)])
        collection.create_index([("annee", 1)])
        collection.create_index([("domain", 1)])
        collection.create_index([("domaine", 1)])
        collection.create_index([("journal", 1)])
        collection.create_index([
            ("title", TEXT),
            ("titre", TEXT),
            ("abstract", TEXT),
            ("resume", TEXT),
        ], name="text_all", default_language="english")
    except Exception:
        # Les erreurs d'index non critiques ne doivent pas bloquer l'app
        pass

    # ========================
    # HELPER (mapping FR -> EN)
    # ========================
    def normalize(doc):
        return {
            "title": doc.get("title") or doc.get("titre"),
            "year": doc.get("year") or doc.get("annee"),
            "domain": doc.get("domain") or doc.get("domaine"),
            "journal": doc.get("journal"),
            "abstract": doc.get("abstract") or doc.get("resume"),
            "authors": doc.get("authors") or doc.get("auteurs"),
            "doi": doc.get("doi")
        }

    # ========================
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    # ========================
    @app.get("/articles")
    def get_articles():
        limit = request.args.get("limit", default="100")

        try:
            limit_int = max(1, min(int(limit), 1000))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

        docs = list(collection.find({}, {"_id": 0}).limit(limit_int))
        return jsonify([normalize(d) for d in docs])

    # ========================
    @app.post("/seed")
    def seed_articles():
        """
        Insère des données de test.
        - Option 1: POST JSON {"articles": [ ...documents... ]}
        - Option 2: Sans corps -> insère un petit jeu d'exemple
        """
        body = request.get_json(silent=True) or {}
        articles = body.get("articles")

        if not articles:
            articles = [
                {
                    "title": "AI for Cancer Detection",
                    "auteurs": ["Alice Martin", "Bob Lee"],
                    "resume": "Deep learning models for early cancer detection.",
                    "doi": "10.1000/test-ai-cancer",
                    "journal": "Journal of Medical AI",
                    "date": "2024-03-14",
                    "annee": 2024,
                    "domaine": "Oncology"
                },
                {
                    "titre": "CRISPR Advances",
                    "authors": ["Nina Patel"],
                    "abstract": "CRISPR therapies for rare diseases.",
                    "doi": "10.1000/test-crispr",
                    "journal": "Genomics Today",
                    "date": "2023-11-02",
                    "year": 2023,
                    "domain": "Genetics"
                },
                {
                    "title": "mRNA Vaccine Stability",
                    "authors": ["Karim Othman"],
                    "abstract": "Storage constraints of mRNA vaccines.",
                    "doi": "10.1000/test-mrna",
                    "journal": "Vaccine Research",
                    "date": "2024-01-20",
                    "year": 2024,
                    "domain": "Immunology"
                }
            ]

        if not isinstance(articles, list):
            return jsonify({"error": "Body must contain 'articles' as a list"}), 400

        if not articles:
            return jsonify({"inserted": 0})

        result = collection.insert_many(articles)
        return jsonify({"inserted": len(result.inserted_ids)})

    # ========================
    @app.get("/filter")
    def filter_articles():
        query = {}

        year = request.args.get("year")
        domain = request.args.get("domain")

        conditions = []
        if year:
            try:
                y = int(year)
                conditions.append({"$or": [{"year": y}, {"annee": y}]})
            except ValueError:
                return jsonify({"error": "year must be integer"}), 400

        if domain:
            conditions.append({"$or": [{"domain": domain}, {"domaine": domain}]})

        if len(conditions) == 1:
            query = conditions[0]
        elif len(conditions) > 1:
            query = {"$and": conditions}

        docs = list(collection.find(query, {"_id": 0}))
        return jsonify([normalize(d) for d in docs])

    # ========================
    @app.get("/search")
    def search_articles():
        keyword = request.args.get("q")

        if not keyword:
            return jsonify({"error": "Missing query param: q"}), 400

        # Prefer text search if index exists; fallback to regex if it fails
        try:
            docs = list(
                collection.find(
                    {"$text": {"$search": keyword}},
                    {"_id": 0, "score": {"$meta": "textScore"}}
                ).sort([("score", {"$meta": "textScore"})]).limit(100)
            )
        except Exception:
            query = {
                "$or": [
                    {"title": {"$regex": keyword, "$options": "i"}},
                    {"titre": {"$regex": keyword, "$options": "i"}},
                    {"abstract": {"$regex": keyword, "$options": "i"}},
                    {"resume": {"$regex": keyword, "$options": "i"}}
                ]
            }
            docs = list(collection.find(query, {"_id": 0}).limit(100))
        return jsonify([normalize(d) for d in docs])

    # ========================
    @app.get("/stats/year")
    def stats_year():
        pipeline = [
            {
                "$group": {
                    "_id": {"$ifNull": ["$year", "$annee"]},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        return jsonify(list(collection.aggregate(pipeline)))

    # ========================
    @app.get("/stats/domain")
    def stats_domain():
        pipeline = [
            {
                "$group": {
                    "_id": {"$ifNull": ["$domain", "$domaine"]},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]
        return jsonify(list(collection.aggregate(pipeline)))

    # ========================
    @app.get("/stats/journal")
    def stats_journal():
        pipeline = [
            {"$group": {"_id": "$journal", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        return jsonify(list(collection.aggregate(pipeline)))

    # ========================
    @app.get("/stats/summary")
    def stats_summary():
        # Unifie 'domain' et 'domaine' pour compter correctement les domaines uniques
        try:
            domains_en = set(collection.distinct("domain"))
            domains_fr = set(collection.distinct("domaine"))
            total_domains = len((domains_en | domains_fr) - {None, ""})
        except Exception:
            total_domains = len(collection.distinct("domaine"))
        return jsonify({
            "total_articles": collection.count_documents({}),
            "total_domains": total_domains,
            "total_journals": len(collection.distinct("journal"))
        })

    # ========================
    @app.get("/stats/trends")
    def stats_trends():
        pipeline = [
            {
                "$group": {
                    "_id": {"$ifNull": ["$year", "$annee"]},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        data = list(collection.aggregate(pipeline))

        years = [d["_id"] for d in data if d["_id"] is not None]
        counts = [d["count"] for d in data if d["_id"] is not None]

        result = []
        for i in range(len(years)):
            current = counts[i]
            prev = counts[i - 1] if i > 0 else None

            growth_abs = current - prev if prev is not None else None
            growth_pct = round((growth_abs / prev) * 100, 2) if prev and prev != 0 else None

            result.append({
                "year": years[i],
                "count": current,
                "growth_abs": growth_abs,
                "growth_pct": growth_pct
            })

        peak = max(result, key=lambda x: x["count"]) if result else None

        return jsonify({
            "data": result,
            "peak_year": peak
        })

    print(app.url_map)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app = create_app()
    app.run(host="0.0.0.0", port=port)