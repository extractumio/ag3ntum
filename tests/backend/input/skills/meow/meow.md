---
name: meow
description: Fetches random cat facts from the MeowFacts API.
allowed-tools: Read, WebFetch
---

# Meow Facts Skill

## Instructions

1. Run the `meow.py` script to get a random cat fact
2. Use `--count N` argument to get multiple facts
3. The script outputs JSON with cat facts

## Usage

Run from workspace root. The script path is relative to the skill folder structure.

Get a single cat fact:

```bash
python ./skills/meow/scripts/meow.py
```

Get multiple cat facts:

```bash
python ./skills/meow/scripts/meow.py --count 3
```

Note: The script reads templates from `./skills/meow/templates/` relative to workspace.

## API Details

- **Endpoint**: https://meowfacts.herokuapp.com/
- **Method**: GET
- **Parameters**: 
  - `count` (optional): Number of facts to retrieve

## Example Response

```json
{
  "data": [
    "Mother cats teach their kittens to use the litter box."
  ]
}
```
