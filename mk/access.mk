# Access control per Role (T22): per-Role views, LOGIN roles and GRANTs (sql/access/roles.sql),
# from the user= and password= of DATABASE_URL_ENGINEER, DATABASE_URL_TEAM_LEAD and
# DATABASE_URL_MANAGER. Run it after `make derive`, which drops the views (docs/access-control.md).

.PHONY: access access-check access-drop

access: ## Create the per-Role views and LOGIN roles from DATABASE_URL_<ROLE>, then check them as each login
	$(UV) run python scripts/access.py

access-check: ## Check, as each Role's login, what it may read (its own views only)
	$(UV) run python scripts/access.py --check

access-drop: ## Drop the per-Role views and LOGIN roles
	$(UV) run python scripts/access.py --drop
