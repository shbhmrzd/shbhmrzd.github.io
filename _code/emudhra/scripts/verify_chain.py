import sys, re, hashlib
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding

pems = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", open(sys.argv[1]).read(), re.S)
leaf, inter, root = [x509.load_pem_x509_certificate(p.encode()) for p in pems]

def check(child, parent, data=None):
    data = data if data is not None else child.tbs_certificate_bytes
    try:
        parent.public_key().verify(child.signature, data, padding.PKCS1v15(), child.signature_hash_algorithm)
        return "VALID"
    except Exception as e:
        return "INVALID"

print("leaf says:", leaf.subject.rfc4514_string(), "| signed by:", leaf.issuer.rfc4514_string())
print("bytes the CA signed (the certificate body):", len(leaf.tbs_certificate_bytes))
print("SHA-256 of those bytes:", hashlib.sha256(leaf.tbs_certificate_bytes).hexdigest()[:32] + "...")
print("signature attached to the leaf:", len(leaf.signature), "bytes,", leaf.signature.hex()[:32] + "...")
print()
print("check leaf  with YR1's public key    :", check(leaf, inter))
print("check YR1   with Root YR's public key:", check(inter, root))
print()
body = bytearray(leaf.tbs_certificate_bytes)
i = bytes(body).find(b"github.io")
body[i:i+9] = b"evilxx.io"
print("same leaf, but 'github.io' changed to 'evilxx.io' in the body:", check(leaf, inter, bytes(body)))
print("leaf checked with the wrong parent (Root YR instead of YR1)  :", check(leaf, root))
