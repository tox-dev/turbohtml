The URL language filter now reads a percent-encoded unreserved character in a leading path segment as its decoded form,
so ``/%64e/`` marks German and is rejected for English. Escapes of other characters, such as ``%2F``, stay literal as
before.
