set -e
O=/opt/homebrew/bin/openssl
# print only the "priv:" block of a key, the format shown in the post
show_priv() { $O pkey -in "$1" -noout -text | awk '/^pub:/{exit} /^priv:/{f=1} f'; }

printf "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n" > ca.ext
printf "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:myblog.com\n" > leaf.ext

echo "== root =="
$O req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-384 -noenc -keyout root.priv -out root.crt -subj "/CN=Toy Root CA" -days 365 \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

echo "== normal intermediate =="
$O genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-384 -out inter.priv
echo "random P-384 private key (different on every run):"
show_priv inter.priv
$O req -new -key inter.priv -subj "/CN=Toy Intermediate CA" -out inter.csr
$O x509 -req -in inter.csr -CA root.crt -CAkey root.priv -extfile ca.ext -days 365 -out inter.crt

echo "== myblog.com signed by normal intermediate =="
$O genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out myblog.priv
$O req -new -key myblog.priv -subj "/CN=myblog.com" -out myblog.csr
$O x509 -req -in myblog.csr -CA inter.crt -CAkey inter.priv -extfile leaf.ext -days 90 -out myblog.crt
$O verify -CAfile root.crt -untrusted inter.crt myblog.crt

echo "== weak intermediate, d = 156 =="
python3 -c "
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization as s
k = ec.derive_private_key(156, ec.SECP384R1())
open('weak.priv','wb').write(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))"
echo "the d = 156 private key:"
show_priv weak.priv
$O req -new -key weak.priv -subj "/CN=Toy Weak Intermediate CA" -out weak.csr
$O x509 -req -in weak.csr -CA root.crt -CAkey root.priv -extfile ca.ext -days 365 -out weak.crt
$O x509 -req -in myblog.csr -CA weak.crt -CAkey weak.priv -extfile leaf.ext -days 90 -out myblog-weak.crt
$O verify -CAfile root.crt -untrusted weak.crt myblog-weak.crt
