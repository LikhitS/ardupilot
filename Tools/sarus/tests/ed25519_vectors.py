# Writes ed25519_vectors.h for ed25519_check.cpp: the first three RFC 8032 section 7.1 vectors, plus
# signatures made just now by Python's cryptography package over random messages.
import os
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

rfc = [
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
]
cases = [(pk, msg, sig, 1) for _, pk, msg, sig in rfc]
for n in (35, 1, 200):
    k = Ed25519PrivateKey.generate()
    pk = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    m = os.urandom(n)
    s = bytearray(k.sign(m))
    cases.append((pk, m.hex(), s.hex(), 1))
    bad = bytearray(s); bad[5] ^= 1
    cases.append((pk, m.hex(), bad.hex(), 0))
    cases.append((pk, (bytes([m[0] ^ 0x80]) + m[1:]).hex(), s.hex(), 0))
# a valid signature checked against the wrong public key must fail
cases.append((rfc[1][1], rfc[0][2], rfc[0][3], 0))

def arr(hexs):
    b = bytes.fromhex(hexs)
    return '{' + ','.join(str(x) for x in b) + '}' if b else '{0}'

with open(os.path.join(os.path.dirname(__file__), 'ed25519_vectors.h'), 'w') as f:
    f.write('struct tcase { unsigned char pk[32]; unsigned char msg[256]; unsigned msglen; unsigned char sig[64]; int expect; };\n')
    f.write('static const tcase cases[] = {\n')
    for pk, msg, sig, exp in cases:
        f.write('  {%s, %s, %d, %s, %d},\n' % (arr(pk), arr(msg), len(msg) // 2, arr(sig), exp))
    f.write('};\n')
print(len(cases), 'cases')
