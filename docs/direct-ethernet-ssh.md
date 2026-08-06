# Low-latency SSH over direct Ethernet

Connect a Mac and an Ubuntu machine with a dedicated Ethernet link so local SSH and file transfers avoid Wi-Fi. Both machines can keep using Wi-Fi for internet access and Tailscale for remote access.

```text
             Wi-Fi
Mac ---------------------- Router/Internet
 |
 | Direct Ethernet
 |
Ubuntu machine ----------- Router/Internet
             Wi-Fi
```

The Ethernet interfaces use static addresses without a gateway. This creates a route for `10.77.77.0/24` over Ethernet while the existing default route continues to use Wi-Fi. No bridge, NAT, Internet Sharing, or router configuration is needed.

## Ubuntu setup

Find the Ethernet interface:

```sh
nmcli device status
```

In the commands below, replace `<ethernet-interface>` with the reported name, such as `enp13s0`:

```sh
sudo nmcli connection add \
  type ethernet \
  ifname <ethernet-interface> \
  con-name mac-direct \
  ipv4.method manual \
  ipv4.addresses 10.77.77.2/24 \
  ipv4.never-default yes \
  ipv6.method disabled

sudo nmcli connection up mac-direct
```

`ipv4.never-default yes` prevents the Ethernet connection from replacing Wi-Fi as the default internet route.

Verify the routes:

```sh
ip route
```

The output should contain a default route over Wi-Fi and a route similar to:

```text
10.77.77.0/24 dev <ethernet-interface>
```

## macOS setup

Connect the Ethernet cable, using a USB-C Ethernet adapter if needed. Open **System Settings → Network → Ethernet → Details → TCP/IP**, then configure:

- Configure IPv4: **Manually**
- IP address: `10.77.77.1`
- Subnet mask: `255.255.255.0`
- Router: leave blank
- DNS: leave blank/default

Test the direct link:

```sh
ping 10.77.77.2
```

A wired point-to-point connection should have low and consistent latency, though the exact result depends on the adapters and hardware.

## SSH configuration

Ensure the SSH server is installed and running on Ubuntu:

```sh
sudo apt install openssh-server
sudo systemctl enable --now ssh
```

Use SSH keys rather than storing a password in configuration. Add entries like these to `~/.ssh/config` on the Mac, replacing `<ubuntu-user>` and `<tailscale-ip>` with local values:

```sshconfig
Host ai-box
    HostName 10.77.77.2
    User <ubuntu-user>
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3

Host ai-box-remote
    HostName <tailscale-ip>
    User <ubuntu-user>
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3

Host *
    AddKeysToAgent yes
    UseKeychain yes
    IdentityFile ~/.ssh/id_ed25519
```

Connect locally over Ethernet or remotely over Tailscale:

```sh
ssh ai-box
ssh ai-box-remote
```

The same aliases work with file-transfer tools:

```sh
scp ./file ai-box:~/
rsync -av ./directory/ ai-box:~/directory/
```

Do not commit private keys, passwords, public Tailscale host details, or a populated personal SSH configuration to this repository.

## Undo the Ubuntu configuration

Remove the NetworkManager profile:

```sh
sudo nmcli connection delete mac-direct
```

On the Mac, remove the manual Ethernet configuration or change **Configure IPv4** back to **Using DHCP**.
