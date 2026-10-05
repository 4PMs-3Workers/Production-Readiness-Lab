# Git Flow

## Workflow Overview

```text
main
 └─ develop
     └─ feature/*
         └─ develop
             └─ release/*
                 └─ main
```

## Branch Roles

- `main`: protected production branch containing stable and released code.
- `develop`: protected integration branch for ongoing development and pre-release validation.
- `feature/*`: branches for implementing new features, created from `develop`.
- `release/*`: branches for preparing a production release, created from `develop`.
- `hotfix/*`: branches for urgent production fixes, created from `main`.

## Branch Naming Convention

```bash
feature/feature-name
release/release-name
fixbug/bug-description
hotfix/hotfix-name
```

## Recommended Workflow

### 1. Start a new feature

```bash
git checkout develop
git pull origin develop
git checkout -b feature/feature-name
```

### 2. Work on the feature

```bash
git add .
git commit -m "feat: short and clear summary"
git push origin feature/feature-name
```

### 3. Merge feature into develop

- Create a Pull Request from `feature/feature-name` to `develop`.
- Require review and approval before merging.
- CI/CD will run automatically after the Pull Request is merged.

### 4. Prepare a release

```bash
git checkout develop
git pull origin develop
git checkout -b release/release-name
```

- Perform final validation and release preparation on this branch.
- Create a Pull Request from `release/release-name` to `main` for production deployment.
- After the release is merged into `main`, create another Pull Request from `release/release-name` back to `develop` to sync release fixes.
- CI/CD will run automatically after each Pull Request merge.

### 5. Handle a hotfix

```bash
git checkout main
git pull origin main
git checkout -b hotfix/hotfix-name
```

- Create a Pull Request from `hotfix/hotfix-name` to `main`.
- After the hotfix is merged into `main`, create a Pull Request from `hotfix/hotfix-name` back to `develop`.

## Notes

- Direct pushes to `main` and `develop` are prohibited.
- All code changes must be introduced through Pull Requests.
- Use `feature/*` for new functionality.
- Use `release/*` for stabilization before production deployment.
- Use `fixbug/*` for bug fixes discovered during development.
- Use `hotfix/*` for urgent production incidents.
