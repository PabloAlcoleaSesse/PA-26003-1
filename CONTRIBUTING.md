# Contributing

Thanks for your interest in contributing.

## Before You Start

- Open an issue to discuss substantial changes before implementing them.
- Keep pull requests focused on one topic.
- Follow the repository's Code of Conduct.

## Development Setup

1. Fork and clone the repository.
2. Copy environment variables:
   ```bash
   cp .env.example .env
   ```
3. Build and run services:
   ```bash
   make up
   make init-db
   ```

## Making Changes

- Use clear, descriptive commit messages.
- Update documentation when behavior or interfaces change.
- Keep backwards compatibility unless the change is explicitly breaking.

## Validation

Run the existing project workflow before opening a pull request:

```bash
make run-all
```

## Pull Requests

- Fill in the pull request template completely.
- Reference related issues in the description.
- Ensure CI checks pass.
