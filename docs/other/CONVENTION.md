# Frontend Coding Convention

## 1. General Principles

- Write maintainable, accessible, and reusable UI code.
- Use English names for files, components, hooks, and variables.
- Keep components small and focused on a single responsibility.
- Prefer the Next.js App Router.
- Use TypeScript in strict mode.

## 2. Style and Formatting

- Follow the project's linting and formatting rules.
- Use consistent indentation and import ordering.
- Keep code readable and avoid unnecessary complexity.

## 3. Next.js Guidelines

- Use the `app/` directory for route-based structure.
- Keep page files focused on composition.
- Use `layout.tsx` for shared layout structure.
- Use `next/link` for internal navigation.
- Use `next/image` for image optimization.
- Prefer server components by default and use client components only when needed.

## 4. Project Structure

```text
frontend/
  app/
  components/
  hooks/
  lib/
  services/
  types/
  styles/
  public/
```

## 5. Naming Conventions

- Use `kebab-case` for folders and files.
- Use `PascalCase` for React component names.
- Use `camelCase` for variables, hooks, and functions.
- Use `UPPER_SNAKE_CASE` for constants.

## 6. Component Rules

- Keep components stateless where possible.
- Pass props clearly and explicitly.
- Prefer TypeScript interfaces for props.
- Split large components into smaller reusable ones.

## 7. API Integration

- Centralize API calls in the `services/` folder.
- Handle loading, error, and empty states consistently.
- Keep UI components independent from raw API implementation details.

## 8. Testing

- Test critical user flows and key UI behavior.
- Use React Testing Library and Jest or Playwright where appropriate.

# Backend Coding Convention

## 1. General Principles

- Follow Python 3.11+ best practices.
- Write clean, readable, and testable code.
- Use English names for modules, variables, functions, and classes.
- Prefer small functions with a single responsibility.
- Use type hints for function signatures and return values.

## 2. Style and Formatting

- Follow PEP 8.
- Use 4 spaces for indentation.
- Keep line length around 100–120 characters.
- Format code with `black` and lint with `ruff` or `flake8`.

## 3. FastAPI Guidelines

- Keep API handlers thin and focused on request/response handling.
- Put business logic in services.
- Use Pydantic models for request and response validation.
- Use dependency injection for shared services and authentication.
- Return explicit HTTP status codes.
- Use `HTTPException` for expected application errors.
- Prefer `async def` for I/O-bound operations.

## 4. Project Structure

```text
backend/
  app/
    api/
    core/
    models/
    schemas/
    services/
    repositories/
    utils/
    main.py
  tests/
```

## 5. Naming Conventions

- Use `snake_case` for files, variables, and functions.
- Use `PascalCase` for classes.
- Use `UPPER_SNAKE_CASE` for constants.
- Use descriptive names such as `create_user`, `get_order_detail`, and `auth_service`.

## 6. Testing

- Write unit tests for services and utilities.
- Write integration tests for API endpoints.
- Use `pytest` and `httpx` or `TestClient`.
- Name test files as `test_*.py`.
