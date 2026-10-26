import sys, time
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

cert = x509.load_pem_x509_certificate(open(sys.argv[1], "rb").read())
pub = cert.public_key().public_numbers()
print("Subject:", cert.subject.rfc4514_string())
print("Public key x:", hex(pub.x)[:20] + "...")

start = time.perf_counter()
for d in range(1, 256):
    candidate = ec.derive_private_key(d, ec.SECP384R1()).public_key().public_numbers()
    if (candidate.x, candidate.y) == (pub.x, pub.y):
        print(f"Found private key d = {d} after {d} tries in {(time.perf_counter()-start)*1000:.2f} ms")
        key = ec.derive_private_key(d, ec.SECP384R1())
        msg = b"signed with the recovered key"
        sig = key.sign(msg, ec.ECDSA(hashes.SHA384()))
        cert.public_key().verify(sig, msg, ec.ECDSA(hashes.SHA384()))
        print("Signature made with d verifies against the certificate's public key: OK")
        break
else:
    print("No key below 256")
