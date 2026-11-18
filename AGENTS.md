# Kaibun deployment

When helping a user run this project, read README.md and docs/DEPLOYMENT.md first, inspect the actual environment, and follow the staged checks. This is a standalone local application: do not apply Eidolon-SC's Docker, OAuth, S3, database, story-embedding or 8765-server instructions to it.

Use the existing user's authorization to complete requested setup. Keep secrets in the ignored local .env; never paste their values into logs or chat. Optional model indexing and voice synthesis use paid external services: explain which selected operation sends text/images before performing it. Do not download the full asset catalog unless the user requested it; start with their selected characters/default outfits.

Use scripts/doctor.py under backend for offline checks. A successful health check does not prove the configured model or voices work: verify a short story when the user authorizes actual use. Do not include test stories, credentials, runtime connection tokens, caches or downloaded proprietary assets in a source release. Preserve source attribution and licenses.

For local wardrobe setup, outfit selection or pose previews, follow docs/OUTFITS_AND_PREVIEWS.md. Do not replace a user's complete seasonal outfit table with a partial browser export.
