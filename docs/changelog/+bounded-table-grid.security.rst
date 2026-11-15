Table extraction now raises ``ValueError`` once spans and row padding add more than 4,000,000 cells to a call's grid,
instead of allocating gigabytes.
