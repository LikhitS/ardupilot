// Checks AP_SarusLock's Ed25519 verifier, built with ArduPilot's own monocypher.cpp, against the
// RFC 8032 vectors and signatures from Python's cryptography package (ed25519_vectors.py).
// The Sarus firmware CI builds and runs it on every push.
#include <stdio.h>
#include <AP_SarusLock/monocypher_ed25519.h>
#include "ed25519_vectors.h"

int main()
{
    int fails = 0;
    const int n = sizeof(cases) / sizeof(cases[0]);
    for (int i = 0; i < n; i++) {
        const tcase &c = cases[i];
        // crypto_ed25519_check returns 0 for a good signature
        const int good = crypto_ed25519_check(c.sig, c.pk, c.msg, c.msglen) == 0;
        // the streaming form AP_SarusLock uses must agree
        crypto_check_ed25519_ctx ctx {};
        crypto_check_ctx_abstract *actx = (crypto_check_ctx_abstract *)&ctx;
        crypto_ed25519_check_init(actx, c.sig, c.pk);
        crypto_ed25519_check_update(actx, c.msg, c.msglen);
        const int good2 = crypto_ed25519_check_final(actx) == 0;
        const bool ok = good == c.expect && good2 == c.expect;
        printf("case %2d expect %d got %d/%d %s\n", i, c.expect, good, good2, ok ? "ok" : "FAIL");
        fails += ok ? 0 : 1;
    }
    printf("%s: %d of %d cases\n", fails ? "FAILED" : "PASS", n - fails, n);
    return fails ? 1 : 0;
}
