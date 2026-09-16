#!/usr/bin/env bash
set -euo pipefail
# Run from a clone, or use the GitHub bootstrap command documented in README.
APP_DIR=/opt/line-converter
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LAN_IF=$(ip -4 route show table main default | awk 'NR==1 {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1);exit}}')
LAN_IP="${LINE_LAN_IP:-$(ip -4 -o addr show dev "$LAN_IF" scope global | awk 'NR==1 {split($4,a,"/");print a[1]}')}"
ACCEL_COMMIT=c9d938173838d6955c824d141f24672d7037eb4e
[[ $EUID -eq 0 ]] || { echo '请以 root 运行'; exit 1; }
[[ $(uname -s) == Linux ]] || { echo '仅支持 Linux'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-yaml curl ca-certificates unzip git cmake build-essential libssl-dev libpcre2-dev iptables nftables iproute2 kmod
modprobe pptp
modprobe nft_tproxy
modprobe nft_ct
install -d -m 755 "$APP_DIR" "$APP_DIR/web"
install -d -m 700 "$APP_DIR/data"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
    install -m 644 "$SOURCE_DIR"/*.py "$APP_DIR/"
    install -m 644 "$SOURCE_DIR"/web/* "$APP_DIR/web/"
    install -m 755 "$SOURCE_DIR/install.sh" "$APP_DIR/install.sh"
fi
if [[ ! -x "$APP_DIR/accel/sbin/accel-pppd" ]]; then
    BUILD_DIR=$(mktemp -d)
    git clone https://github.com/accel-ppp/accel-ppp.git "$BUILD_DIR/accel"
    git -C "$BUILD_DIR/accel" checkout "$ACCEL_COMMIT"
    cmake -S "$BUILD_DIR/accel" -B "$BUILD_DIR/build" -DCMAKE_INSTALL_PREFIX="$APP_DIR/accel" -DRADIUS=FALSE -DSHAPER=FALSE -DBUILD_IPOE_DRIVER=FALSE
    cmake --build "$BUILD_DIR/build" -j2
    cmake --install "$BUILD_DIR/build"
fi
# Independent pinned core; never overwrite existing Xray / Mihomo installations.
CORE_BINARY="$APP_DIR/bin/sing-box"
if [[ ! -x "$CORE_BINARY" ]]; then
    case "$(uname -m)" in
        aarch64) ARCH=arm64; CORE_SHA=6060b42fa84c5dcaeae1799af7f61b0f1ae4855d9d5ddc9e02baba17154b3ae2;;
        armv7l) ARCH=armv7; CORE_SHA=f2c8af2e3576f40f8ab0d06e1d44840e4eb6bcf410ba8d42381781cb0d6fe41b;;
        x86_64) ARCH=amd64; CORE_SHA=12cb2816b52febb356f6a885b740cc8758c3f30b8ae0ca8edba80f0d2d35343f;;
        *) echo '不支持的 CPU 架构'; exit 1;;
    esac
    CORE_VERSION=1.14.1
    ASSET="sing-box-$CORE_VERSION-linux-$ARCH"
    install -d "$APP_DIR/bin"
    curl --fail --location --retry 3 "https://github.com/SagerNet/sing-box/releases/download/v$CORE_VERSION/$ASSET.tar.gz" -o "$APP_DIR/bin/core.tar.gz"
    echo "$CORE_SHA  $APP_DIR/bin/core.tar.gz" | sha256sum --check --status
    tar -xzf "$APP_DIR/bin/core.tar.gz" -C "$APP_DIR/bin" --strip-components=1 "$ASSET/sing-box"
    chmod +x "$CORE_BINARY"
fi
cat > /etc/line-converter.env <<EOF
LINE_LIVE=1
LINE_DATA=$APP_DIR/data
LINE_BIND=$LAN_IP
LINE_LAN_IP=$LAN_IP
LINE_PORT=8088
LINE_CORE=$CORE_BINARY
EOF
chmod 600 /etc/line-converter.env
cat > /etc/systemd/system/line-converter-network.service <<EOF
[Unit]
Description=Line Converter private routing rules
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $APP_DIR/network.py
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/line-converter-core.service <<EOF
[Unit]
Description=Line Converter sing-box
After=line-converter-network.service
Requires=line-converter-network.service
[Service]
ExecStart=$CORE_BINARY run -c $APP_DIR/data/core.json
Restart=on-failure
RestartSec=3
UMask=0077
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/line-converter-pptp.service <<EOF
[Unit]
Description=Line Converter PPTP
After=line-converter-network.service
Requires=line-converter-network.service
[Service]
ExecStart=$APP_DIR/accel/sbin/accel-pppd -c $APP_DIR/data/accel.conf
Restart=on-failure
RestartSec=3
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/line-converter.service <<EOF
[Unit]
Description=Line Converter Python Console
After=network-online.target line-converter-network.service
Requires=line-converter-network.service
[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/line-converter.env
ExecStart=/usr/bin/python3 $APP_DIR/app.py
Restart=on-failure
RestartSec=5
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/logrotate.d/line-converter <<EOF
$APP_DIR/data/accel*.log {
    weekly
    rotate 4
    size 5M
    missingok
    notifempty
    copytruncate
}
EOF
systemctl daemon-reload
systemctl enable line-converter-network.service
systemctl restart line-converter-network.service
systemctl enable line-converter.service
systemctl restart line-converter.service
sleep 2
systemctl is-active --quiet line-converter.service
echo "安装完成，控制台：http://$LAN_IP:8088"
echo 'PPTP：vpn1 起；统一密码：123456；导入订阅后自动创建账号。'
