# Contributing

Thanks for looking at seamproof. Issues and pull requests are welcome.

## Before you start

- For a starter issue, claim it with a comment first, so two people do not build the same thing.
- Keep each change small and focused on one issue.

## Running the tests

```console
python3 run_tests.py
```

The full suite (17 tests, stdlib `unittest`) must pass before a pull request is reviewed. If your change alters the CLI or its output, update the README in the same pull request — and SKILL.md too, if install, commands, flags, exit codes or output wording changed.

## Pull requests

- Say what you changed and how you tested it.
- Fixes are judged on behaviour: the tests, and the measured seam scores on the example textures, should show the change does what it claims.
