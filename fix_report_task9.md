# Fix Report: Task 9 - CLI Interface

## Issue 1: Environment variable typo
**Problem:** Environment variable name was `OPENSUP_API_URL` (missing "HI" in "SHIP").
**Fix:** Renamed to `OPENSHIP_API_URL` in:
- `apps/cli/src/main.py` (line 32 - `get_api_url()` function, line 49 - error message)
- `apps/cli/tests/test_cli.py` (line 24 - `test_get_api_url_from_env`)

## Issue 2: Inefficient `tool_describe` implementation
**Problem:** `tool_describe` fetched ALL tools from `/api/tools` and filtered locally in Python.
**Fix:** 
- Added `name` query parameter support to the backend `list_tools_endpoint` in `apps/agent/src/api/routes.py`
- Updated CLI `tool_describe` to pass `name` filter parameter to the API endpoint
- This reduces network overhead and processing time for describe operations

## Tests run

### CLI tests (all 19 passed)
```bash
cd apps/cli && uv run pytest tests/test_cli.py -v
```
- Updated `test_tool_describe_found` and `test_tool_describe_not_found` to verify the API is called with the `name` filter parameter

### Backend API tests (all 12 passed)
```bash
cd apps/agent && uv run pytest tests/test_api.py -v
```
- Added new test `test_list_tools_with_name_filter` to verify the name filter works correctly

## Status: COMPLETE
Both issues fixed, all tests passing.