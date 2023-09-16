# redislocks

- state 0: No readlocks, no write lock, no writewaiter
- state 1: Only read, no write lock, no writewaiter
- state 2: Only write, no readlocks
- state 3: Reading, no write lock, writewaiter exists

