# Test Input Files

This directory contains configuration files and skills used by the E2E tests.

## Structure

```
input/
├── config/
│   ├── agent.yaml      # Agent configuration (haiku model, low max_turns)
│   ├── permissions.yaml # Permission profile with skill support
│   └── api.yaml        # Generated at test runtime with dynamic port
├── skills/
│   └── meow/           # Test skill for validation
│       ├── meow.md
│       ├── scripts/
│       │   └── meow.py
│       └── templates/
│           └── meow.md
└── README.md
```

## Notes

- `secrets.yaml` is copied from the real config at runtime (contains API key)
- `api.yaml` is generated at runtime with a dynamically allocated port
- The meow skill fetches cat facts and is used to test skill execution

