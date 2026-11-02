## What

<!-- One or two lines. Link the Jira task, e.g. TK092. -->

## Covers

<!-- Acceptance criteria from the sprint spec, e.g. AC3. Write "none" for chores. -->

## How to check

<!-- curl calls or test names a reviewer can run, with the expected result. -->

## Checklist

- [ ] Branched from `development`, one task per PR
- [ ] `make lint` and `make test` pass
- [ ] New or changed endpoints documented in `docs/DISPATCH_API.md` (the API contract)
- [ ] New migration included if the schema changed
- [ ] No secrets, camera URLs, model weights or APNs keys committed
- [ ] Nothing outside the spec's scope added
