"""UP-39 / UP-40 / UP-41 — Flask Blueprint package.

Routes are progressively migrated out of the monolithic `app.py` into
focused submodules. Each submodule exports a `bp = Blueprint(...)` and
defines routes against it; `app.py` calls `app.register_blueprint(bp)`
once at boot.

Migration strategy follows the schema-evolution policy:
1. Blueprint scaffolded with a small set of well-bounded routes.
2. Remaining routes (still on the monolith `app`) move over time —
   pairs of `@app.route(...)` decorators get rewritten to `@bp.route(...)`
   and the underlying helper imports change from local to deferred.
3. Helpers used ONLY by blueprint routes move with them; helpers shared
   with `app.py` stay where they are and the blueprint imports them
   lazily inside route bodies to avoid circular imports.

DO NOT consolidate helpers cross-blueprint unless they're genuinely
shared — keep each blueprint self-contained so it can be tested in
isolation.
"""
