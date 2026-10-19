# How the disposable GPU workstation works

A **virtual machine (VM)** is a machine interface on which an operating system
can boot: CPUs, memory, firmware, disks and devices. Its resources are backed by
real hardware, but the guest OS gets a controlled view of them rather than
control of the whole physical PC.

Your existing Ubuntu is the **host**: its kernel owns the physical machine and
runs the virtualization software. The Ubuntu booted inside the VM is the
**guest**: it has its own kernel, users, services and files. Guest root can
administer that machine without acquiring host root privileges.

Three components divide the work: **QEMU provides virtual hardware; KVM runs
the guest's CPU instructions with hardware-enforced isolation; libvirt
configures and manages the machine.** The guest is ordinary Ubuntu with its
own kernel. In this setup, most instructions execute directly rather than
being interpreted by software.

For this project, the goal is a disposable workstation where Codex can install
software and use the RTX 5090. The host keeps control of lifecycle and protection;
the original model library stays outside the guest's writable storage.

**Scope:** this document explains the architecture, using an ordinary VM as
the starting point and GPU assignment, model sharing and disposable storage as
the implemented workstation design. Host setup, guest provisioning and lifecycle
commands are implemented, but have not yet been verified with a running GPU guest
on ai-box. Follow [the operating guide](codex-vm.md) for setup and verification.

## Read the boundaries first: three independent questions

**Root is a user account, not a CPU execution mode.** There are two CPU
execution dimensions, plus the OS's accounting of which user owns a process:

| Question | Possibilities in this x86 setup | What it tells you |
| --- | --- | --- |
| Which execution environment? | Host or guest | Which OS's CPU context is active; AMD calls the guest context SVM guest mode, Intel calls it VMX non-root mode |
| Which CPU privilege level? | User mode (ring 3) or kernel mode (ring 0) | Whether the executing code can perform that environment's privileged kernel operations |
| Which OS user account? | Host `andy`, host `root`, host `libvirt-qemu`; separately guest `codex`, guest `root` | Permissions for files, processes and administration within that OS |

“VMX root” in Intel terminology is unrelated to the Linux `root` account. On
this AMD machine the mechanism is SVM, but the host/guest distinction serves
the same purpose. We use **host/guest** and **user/kernel mode** below rather
than using “root mode” for either dimension.

| Code executing now | Environment | CPU privilege | User identity / host accounting |
| --- | --- | --- | --- |
| Host shell | Host | User mode | Host `andy` |
| `sudo virsh ...` or system libvirt daemon | Host | User mode | Host `root`; becoming UID 0 does not turn its code into kernel code |
| QEMU device-model code | Host | User mode | Normally host `libvirt-qemu` under Ubuntu's system libvirt |
| KVM handling QEMU's request | Host | Kernel mode | Host kernel code executing on behalf of a QEMU thread; not another root-owned daemon |
| Guest Codex or shell | Guest | User mode | Guest `codex`; host scheduler accounts execution to a QEMU vCPU thread |
| Guest `sudo apt ...` | Guest | User mode | Guest `root`; still executing through that same kind of host vCPU thread |
| Guest Linux kernel, including a guest kernel thread | Guest | Kernel mode | Guest kernel code; not a separate host process or host UID 0 execution |

Userland code enters its own OS kernel through system calls. For example,
`apt` running as guest root executes user-mode instructions, then enters the
**guest** kernel to open a file. That does not make a host system call as host
root. A QEMU vCPU thread can move between host user mode, host kernel mode and
guest execution without changing its host account to match the guest user.
Kernel code is not assigned an ordinary user account in the same sense as a
userland process; permissions can still be checked against the calling task's
credentials. Hardware isolation constrains guest ring 0 as well as guest ring 3.

A **process** has an address space, resources and credentials. A **thread** is
one schedulable execution stream in a process; threads share that process's
memory and resources. Linux gives threads their own task IDs (TIDs), while tools
usually group them under one process ID (PID). This distinction matters because
one QEMU process contains several vCPU threads, not one QEMU process per guest
application.

## 1. Start with the machine the guest sees

“Virtual” describes the interface and ownership, not whether real computation or
storage happens. Some resources are multiplexed; others are assigned exclusively.

| Guest resource | What backs it on the host | What this means here |
| --- | --- | --- |
| Virtual CPU (vCPU) | A host thread scheduled onto a physical CPU, using KVM | Eight vCPUs do not automatically reserve eight cores |
| Guest RAM | Host RAM mapped into the VM | Guest addresses are restricted to its assigned memory |
| Virtual disk | A disk image file on a host filesystem | Guest formatting changes that file's contents, not the physical SSD's partition table |
| Virtual NIC | A software device connected to host networking | It has its own MAC/IP without owning a physical NIC |
| Assigned GPU | The physical 5090, connected through PCI passthrough | One guest owns the GPU; the host cannot use it simultaneously |
| Shared model directory | Selected host files exposed through a file-sharing device | Host-enforced read-only access preserves the original models |

```mermaid
flowchart TB
    management["libvirtd: host userland, UID root; reads saved config"] -.->|"starts and configures"| qemu
    subgraph guest["Guest Ubuntu: a separate OS"]
        apps["Applications: guest user mode, UID codex or root"] --> kernel["Guest kernel code and drivers: guest ring 0"]
        kernel --> machine["CPU, RAM, disks and device interfaces"]
    end
    machine -->|"device models and storage backends"| qemu["QEMU: host userland, normally UID libvirt-qemu"]
    machine -->|"hardware-assisted CPU execution and memory isolation"| kvm["KVM: host kernel code"]
    qemu -->|"uses /dev/kvm to run vCPUs"| kvm
    qemu --> storage["Host kernel: filesystem and networking"]
    kvm --> hardware["Physical CPU and RAM"]
    storage --> devices["Physical SSDs and NICs"]
```

Arrows show responsibilities and dependencies, not a process tree or the path of
every instruction. The guest ultimately executes on the physical CPU; the next
sections explain who constructs its environment and what gets saved.

## 2. Who builds, runs and manages that machine?

There are two different jobs: **running** a machine and **managing** it. QEMU
and KVM provide execution; libvirt removes the need to assemble QEMU command
lines, permissions, disks and networking by hand for each start.

| Component | What it is | When it exists/runs |
| --- | --- | --- |
| Host Linux | Kernel controlling the physical CPU, RAM and devices | Throughout the host's uptime |
| KVM (`kvm`, `kvm_amd`) | Host kernel code exposing hardware virtualization through `/dev/kvm`; not a daemon | Available while modules are loaded; live VM state exists while a VM runs |
| QEMU | Host userland program implementing a virtual machine's devices and coordinating execution through KVM | Normally one process per running VM, with multiple threads; absent for stopped guests |
| libvirt | Management library/API plus host daemons; starts QEMU, prepares access to devices and saves configuration | Host services, independent of whether guests are running |
| `virsh` / `virt-install` | Short-lived clients that ask libvirt to manage or create a guest | When you invoke a management command |

### What “library,” “daemon” and “service” literally mean

**Libvirt is a collection of userland software**, not one kernel component.
Its client **library** is executable code in a shared-library file such as
`/usr/lib/x86_64-linux-gnu/libvirt.so.0`. A client such as `/usr/bin/virsh`
loads that library into its own process. The library supplies an API (functions
such as `virDomainCreate`) and sends management requests over a socket to a
libvirt daemon. The library is not itself another process. A local socket such
as `/run/libvirt/libvirt-sock` is a kernel-provided communication endpoint
named in the host filesystem; it is not a config file or guest network NIC.

A **daemon** is a userland process intended to provide a background function,
often waiting for requests. Being started at boot is common, but not its
definition: it can start manually or on demand. Under systemd a daemon usually
stays in the foreground and systemd supervises it; it need not fork itself into
the background. A **service** here means a systemd unit: a text file describing
commands to run, ordering and supervision. A service can run a long-lived daemon,
or run a short command and finish. Enabling a service arranges activation through
systemd's dependencies, commonly at boot; starting it runs it now. A `.socket`
unit can arrange for systemd to start a daemon when a connection arrives.
See [systemd service units](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).

For the system connection `qemu:///system`, the libvirt management daemon runs
as host root. It prepares access to devices, files and networking, then launches
QEMU, normally under the restricted host account `libvirt-qemu` on Ubuntu.
These remain host **userland** processes. Actual UIDs, capabilities and AppArmor
restrictions depend on configuration; `qemu:///session` instead uses a user's
unprivileged libvirt instance and is not the setup described here. See
[libvirt's QEMU process permissions](https://libvirt.org/drvqemu.html).

Why several daemons? Libvirt can put VM, network, storage and other management
functions in separate processes rather than put them all in `libvirtd`:

| Host userland process | Job |
| --- | --- |
| `libvirtd` | Combined management daemon used by this project's host setup |
| `virtqemud` | QEMU VM management in the alternative modular layout |
| `virtnetworkd` | Virtual network management in that modular layout |
| `virtstoraged` | Storage management in that modular layout |
| `virtlogd` / `virtlockd` | Separate logging / locking helpers where configured |

These are divisions of management work, not CPU execution modes, vCPUs or guest
kernels. Not every installation runs every helper. Splitting functions allows
separate activation and management. QEMU can keep running while its libvirt
management daemon restarts; libvirt does not execute guest instructions.
See [libvirt daemon layouts](https://libvirt.org/daemons.html).

### Who starts what, and under which user?

The arrows below mean **launches/creates**, except the explicitly marked client
request. This is a launch relationship diagram, not a promise about the current
PPID: daemonization, reparenting or systemd integration can change process parents.
Each box identifies code location and account; all process boxes here are host
userland, running in host user mode.

```mermaid
flowchart TB
    systemd["Host userland: systemd, PID 1, UID root"]
    shell["Host userland: shell, UID andy"]
    virsh["Host userland: sudo virsh, UID root; loads libvirt.so"]
    libvirt["Host userland: libvirtd, UID root"]
    firewall["Host userland: nft command, UID root; exits after setup"]
    subgraph qemu["Host userland process: QEMU, normally UID libvirt-qemu"]
        main["Main / device event-loop thread"]
        cpu0["vCPU 0 host thread"]
        cpu1["vCPU 1 host thread; more for other vCPUs"]
        io["Other worker / I/O threads as configured"]
    end
    systemd -->|"starts service"| libvirt
    systemd -->|"runs firewall oneshot"| firewall
    shell -->|"invokes sudo"| virsh
    virsh -.->|"management request over local socket"| libvirt
    libvirt -->|"launches QEMU with prepared access and restricted credentials"| main
    main -->|"QEMU creates threads sharing its process"| cpu0
    main -->|"QEMU creates threads sharing its process"| cpu1
    main -->|"QEMU creates threads as needed"| io
```

Conceptually libvirt starts a program using Linux process-creation/exec
facilities; QEMU then creates its threads. The exact fork/exec/helper sequence is
an implementation detail. **Libvirt does not fork a host process for each guest
kernel, guest kernel thread or guest application.** QEMU creates vCPU execution
threads and uses KVM to execute guest code on them. During boot that code includes
the guest kernel, which creates guest PID 1 and later guest processes/threads.
The guest scheduler chooses which guest task runs on each vCPU. A guest kernel
thread such as a guest `kworker` is a guest-scheduled task, distinct from the host
thread carrying the vCPU; their lifetimes and counts do not correspond.

From host `ps`, you see QEMU and its host threads, not a separate host PID for
each guest task. From guest `ps`, you see guest systemd, sshd, shells and kernel
threads. Guest PID 1 is not host PID 1. Host kernel helpers such as vhost workers
may also exist, but they are host kernel tasks, not the guest kernel.

Illustrative output only (PIDs and names vary; this is not captured from your PC):

```text
HOST: ps -eo user,pid,ppid,comm
root              1       0 systemd
root            900       1 libvirtd
libvirt-qemu    4200       1 qemu-system-x86

HOST: ps -T -p 4200 -o user,pid,tid,comm
libvirt-qemu    4200    4200 qemu-system-x86
libvirt-qemu    4200    4205 CPU 0/KVM
libvirt-qemu    4200    4206 CPU 1/KVM

GUEST: ps -eo user,pid,ppid,comm
root              1       0 systemd
root            700       1 sshd
codex           950     940 bash
```

### Why a firewall “service” appears in this explanation

The guest has a network route to the host and potentially other LAN machines.
VM memory isolation alone does not prevent it from connecting to host SSH or
other network services. Host firewall rules impose that additional boundary.
This project's `codex-vm-firewall.service` is **not a firewall daemon** and is
separate from libvirt's management daemons. Its unit file runs the host `nft`
program as root to load rules into the host kernel's Netfilter subsystem.
The command exits; host kernel code subsequently checks packets. No userland
firewall process has to remain alive for those rules to apply.

This is an excerpt of the actual
[unit file](../provision/codex-vm/codex-vm-firewall.service), installed as
`/etc/systemd/system/codex-vm-firewall.service`:

```ini
[Unit]
Before=libvirtd.service
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/codex-vm/firewall.nft
ExecReload=/usr/sbin/nft -f /etc/codex-vm/firewall.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

A system service with no `User=` normally runs as host root. `RemainAfterExit=yes`
means systemd records this unit as active after the command exits; it does not
mean `nft` stays running. The host setup also installs a libvirtd dependency with
`Requires=codex-vm-firewall.service` and `After=codex-vm-firewall.service`, so
starting libvirtd pulls in and waits for the firewall setup. Ordering alone
(`Before=`) would not start another unit. The rules admit selected guest DHCP/DNS
traffic and replies, and block other guest-initiated host access and nonpublic
forwarding destinations. Libvirt configures its network/NAT rules separately.
Neither set of rules executes the guest CPU.

**Is QEMU the hypervisor?** People call the combination **QEMU/KVM** the
hypervisor. KVM handles protected CPU execution in the kernel; QEMU supplies
userland machine/device support; libvirt manages them. It is not all implemented
inside one userland process. See [QEMU's overview](https://www.qemu.org/docs/master/system/introduction.html).

## 3. A stopped VM is configuration and stored data

A VM needs to survive shutdown without keeping its execution machinery alive.
It therefore separates the **recipe for a machine**, its **disk contents**, and
its temporary **running state**.

A **libvirt definition** is a machine description in XML: name/UUID, CPU and RAM
settings, disk file paths, virtual NIC/MAC, boot firmware and assigned PCI devices.
It is configuration, not running hardware and not the guest OS itself.

`virsh define machine.xml` imports a persistent definition into libvirt. With
Ubuntu's system libvirt, persistent QEMU definitions are normally stored under
`/etc/libvirt/qemu/`. Use `virsh dumpxml NAME` to inspect them and libvirt tools to
edit them; do not edit daemon-owned files behind its back. A different libvirt
network XML describes the bridge, subnet and DHCP configuration. See
[domain XML](https://libvirt.org/formatdomain.html) and
[network XML](https://libvirt.org/formatnetwork.html).

| State | Where it lives | Survives ordinary guest shutdown? |
| --- | --- | --- |
| Machine definition | libvirt's persistent host configuration | Yes |
| Guest partitions, filesystem, OS and files | VM disk image file(s) | Yes |
| Virtual UEFI variables, if UEFI is selected | A separate host NVRAM file | Yes |
| Guest RAM and device state | Host RAM, QEMU and kernel objects | No, unless explicitly saved |
| Virtual CPU registers and virtualization controls | Kernel-managed memory and physical CPU state while executing | No; reconstructed on the next cold start |
| Host models | Original files on `/mnt/data` | Yes; guest reset does not delete them |

Loading KVM enables the host's ability to run VMs. The host kernel does not read
all saved XML files on boot and register permanent “hardware VMs.” Libvirt reads
those definitions; live kernel VM objects are created when QEMU starts a VM.
Guest autostart is an optional libvirt policy; our current guest does not autostart.

### What is literally on disk?

These are typical host paths for system libvirt on Ubuntu, not files all
necessarily present on an unprovisioned host. Image filenames here illustrate
one guest; lifecycle tooling can choose subdirectories and different names.

| Host path | Literal object / contents | Executing code? |
| --- | --- | --- |
| `/usr/bin/qemu-system-x86_64` | Executable file loaded to start the QEMU process | Host userland program |
| `/usr/sbin/libvirtd` | Executable file for the management daemon | Host userland program |
| `/usr/lib/x86_64-linux-gnu/libvirt.so.0` | Shared-library file, possibly a symlink to a versioned file | Host userland code loaded into a client process |
| `/lib/modules/<host-kernel-version>/kernel/arch/x86/kvm/` | Kernel module files such as `kvm.ko` and `kvm-amd.ko`, possibly compressed | Host kernel code once loaded; directory layout can vary |
| `/dev/kvm` | Character-device node exposing KVM's kernel API | Kernel interface, not an executable or disk image |
| `/etc/libvirt/qemu/codex-vm.xml` | Persistent machine definition | XML data, not code |
| `/etc/libvirt/qemu/networks/codex-vm.xml` | Persistent virtual-network definition | XML data |
| `/var/lib/libvirt/images/codex-vm/root.qcow2` | Host file containing virtual disk blocks, including the guest filesystem | Data to the host; contains the guest's executable files too |
| `/var/lib/libvirt/images/codex-vm/seed.iso` | Small disk image containing cloud-init configuration files | Configuration data read by guest software |
| `/etc/codex-vm/firewall.nft` | Text instructions read by `nft` to install kernel packet-filter rules | Configuration data |

For example, a **machine definition** contains elements like these. This is an
illustrative excerpt, not a complete definition to copy and boot:

```xml
<domain type='kvm'>
  <name>codex-vm</name>
  <memory unit='MiB'>16384</memory>
  <vcpu>8</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/var/lib/libvirt/images/codex-vm/root.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='network'>
      <source network='codex-vm'/>
      <model type='virtio'/>
    </interface>
  </devices>
</domain>
```

The `<emulator>` names a **host executable**, `<source file=...>` a **host disk
image**, and `vda` the disk name the **guest** sees (usually `/dev/vda`). The XML
is input to libvirt, not a program the host kernel executes. `type='kvm'` asks
libvirt to launch QEMU with KVM acceleration. It does not make QEMU kernel code.

The separate [network definition](../provision/codex-vm/network.xml) includes:

```xml
<network ipv6='no'>
  <name>codex-vm</name>
  <forward mode='nat'/>
  <bridge name='virbr-codex' stp='on' delay='0'/>
  <!-- DNS, gateway address and DHCP configuration omitted here -->
</network>
```

Libvirt reads this to configure host kernel network objects and NAT, and normally
starts a host userland `dnsmasq` helper for DHCP/DNS. `dnsmasq` is another daemon,
not a virtual CPU or packet-forwarding kernel. Its runtime user depends on the
package and privilege-dropping configuration; inspect it rather than assume root.

Inside the disk image, the **guest** has its own `/boot/vmlinuz-*`, `/sbin/init`,
`/etc/passwd` and `/home/codex`. Those are not the host's same-named files.
QEMU reads disk blocks; guest firmware/bootloader load the guest kernel from
them, then guest Linux loads guest userland programs. The seed ISO instead
contains plain files such as `user-data` and `meta-data`. A minimal illustrative
`user-data` excerpt looks like:

```yaml
#cloud-config
hostname: codex-vm
users:
  - name: codex
    sudo: ALL=(ALL) NOPASSWD:ALL
```

That sudo policy applies to a **guest** user. Cloud-init is guest userland code,
normally invoked as guest root by guest systemd; neither its configuration nor
its name creates a host-root process.

To inspect a provisioned host without changing it:

```sh
sudo virsh --connect qemu:///system dumpxml codex-vm
systemctl cat libvirtd.service codex-vm-firewall.service
systemctl show libvirtd.service -p MainPID -p User -p FragmentPath
ps -eo user,pid,ppid,comm,args
# Example only: replace 4200 with the actual QEMU PID found above.
ps -T -p 4200 -o user,pid,tid,comm
ls -l /dev/kvm /usr/bin/qemu-system-x86_64
```

An empty `User=` for an ordinary system service means the default root account.
Use process output to check actual runtime credentials, including any changes
the daemon makes after launch. To see the other process view, run `ps` **inside**
the guest over SSH.

### Put an operating system on the virtual disk

Defining hardware does not install software. QEMU can present a disk, CPU and
NIC with no operating system installed, just as a physical PC can have an empty
SSD. Something must put partitions, a filesystem and an OS onto that disk.

There are two ordinary ways to put Ubuntu on that virtual machine:

| Installation route | What happens |
| --- | --- |
| Empty disk + Ubuntu installer ISO | Boot the installer in the guest; it partitions/formats the **virtual** disk and installs Ubuntu. Then boot the installed disk. |
| Ubuntu cloud disk image | Start from a disk that Canonical already partitioned, formatted and installed Ubuntu onto; boot that installed OS directly. |

Both run on the same virtual hardware. An ISO is normally installation media;
a cloud disk image is a preinstalled machine disk, often packaged as QCOW2.
QCOW2 describes how virtual disk blocks are stored in a host file. The guest
filesystem, such as ext4, is **inside** those blocks.

```mermaid
flowchart LR
    subgraph installer["Install it yourself"]
        blank["Blank virtual disk + installer ISO"] --> install["Installer: guest userland, UID root"]
        install --> installed["Partitions + filesystem + Ubuntu"]
    end
    subgraph image["Reuse Canonical's installation"]
        download["Download preinstalled Ubuntu disk image"] --> copy["Copy or clone for this guest"]
        copy --> ready["Partitions + filesystem + Ubuntu already present"]
    end
    installed --> boot["Boot installed Ubuntu; configure instance"]
    ready --> boot
```

### Configure an instance without installing Ubuntu again

The preinstalled image is deliberately generic: it cannot already know your
hostname, SSH key or required packages. **Cloud-init** is guest software that
solves this last step by reading instance configuration during boot.

“Cloud” describes the original use case: providers need to start many machines
from one generic image and configure each without clicking through an installer.
It does **not** mean the VM runs remotely, needs an AWS account, or sends its disk
to a cloud service. Cloud-init also supports the **NoCloud** data source: local
files on a small virtual seed disk/CD-ROM containing hostname, public SSH keys
and setup instructions. The current scripts generate that seed ISO and attach
it alongside the Ubuntu disk. See
[Ubuntu's local cloud-init example](https://ubuntu.com/docs/public-images/public-images-how-to/use-local-cloud-init-ds/)
and [NoCloud](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html).

We enlarge the virtual disk's capacity; the guest can then expand its existing
partition/filesystem into that space. No physical SSD is reformatted. On first
boot cloud-init creates/configures the guest user and installs the requested
tools. Subsequent boots normally keep those installed files; many first-boot
steps do not repeat. See [cloud-init](https://docs.cloud-init.io/en/latest/explanation/introduction.html).

## 4. Starting a VM turns that recipe into a running machine

There are two boots, with different owners. The host boots the physical PC;
later, QEMU/KVM provide a machine on which the guest performs its own boot.
Starting a VM does not reboot the host or replace its kernel.

A simplified host boot is firmware → bootloader → host kernel/initramfs → host
root filesystem → systemd/services. The host kernel establishes memory
protection, page tables, exception/interrupt handling and device drivers before
ordinary userland uses them. KVM modules may load during boot or later on demand.

```mermaid
sequenceDiagram
    participant FW as Physical firmware / bootloader
    participant HK as Host Linux kernel
    participant S as Host userland systemd, UID root / PID 1
    participant L as Host userland libvirtd, UID root
    participant Q as Host userland QEMU, UID libvirt-qemu
    participant K as KVM in host kernel
    participant G as Guest CPU context: firmware, kernel, then userland
    FW->>HK: Boot the existing host OS
    HK->>HK: Set up CPU/memory/interrupt handling and drivers
    HK->>S: Start host PID 1
    S->>L: Start or enable management services
    Note over L,G: Stopped VM: XML and disks exist, no guest is executing
    L->>Q: Start guest on user request, or configured autostart
    L->>Q: Provide prepared disk/network/device access
    Q->>K: Open /dev/kvm, create live VM and virtual CPUs
    Q->>K: Register guest RAM mappings and initial CPU state
    Q->>K: Each QEMU vCPU thread calls KVM_RUN
    K->>G: Enter guest execution on the physical CPU
    G->>G: Virtual BIOS/UEFI loads bootloader from virtual disk
    G->>G: Bootloader loads guest kernel and initramfs
    G->>G: Guest kernel sets up its own page tables, traps and drivers
    G->>G: Mount guest root filesystem, start guest systemd
    G->>G: cloud-init configures instance, SSH and other services start
    Note over HK,G: Host and guest kernels now coexist, host remains in control
```

Virtual firmware is code executed by the guest's virtual CPUs; it is not a
second physical motherboard firmware boot. BIOS versus UEFI depends on the VM
configuration. A cloud image skips OS installation, not this guest boot sequence.

**Where privileges enter:** host administration installs services, creates the
bridge/firewall and grants QEMU access to KVM, disks and assigned devices.
Libvirt's privileged service prepares that access, while QEMU normally runs
under the restricted host account `libvirt-qemu`. `/dev/kvm` is a permissioned
kernel interface, not unrestricted access to kernel memory. This setup uses
host `sudo` for management; guest `codex` sudo grants privileges in the guest.

## 5. How the guest executes directly without controlling the host

A virtual CPU is represented by a QEMU host thread plus kernel-maintained state.
QEMU asks KVM to run it; that thread enters the host kernel and then guest CPU
execution. The host scheduler still decides when that thread gets physical CPU
time. Eight vCPUs does not automatically reserve eight physical cores.

There is no extra host “guest kernel thread” created to execute the guest
kernel. The **same vCPU host thread** executes whichever guest context is
scheduled: guest application user mode, a syscall's guest kernel mode, or a guest
kernel task. Other vCPUs can execute other guest tasks concurrently. A guest
syscall changes the guest privilege level; a **VM exit** changes execution from
guest context to host kernel handling. These are different transitions.

Most guest instructions run directly on the physical CPU. Hardware virtualization
adds a distinction between **host execution** and **guest execution**, separate
from ordinary user/kernel privilege levels. The guest can run its own ring-0
kernel without becoming the host's ring-0 kernel.

### Mapping guest memory onto host memory

The guest needs addresses it can manage independently; the host needs to prevent
those addresses from referring to other host processes or the host kernel.

The guest translates a process virtual address into a guest physical address
using its own page tables, selected by its `CR3` register. Hardware nested page
tables translate that into host physical RAM. Translation lookaside buffer
(TLB) caching accelerates this; QEMU does not look up every load/store. A guest mapping
cannot simply name arbitrary host RAM. Ordinary KVM does not hide guest memory
from the host administrator.

The RAM is backed by mappings in QEMU's host address space; KVM manages the
hardware translation to the underlying host pages. See
[KVM memory translation](https://docs.kernel.org/virt/kvm/x86/mmu.html).

```mermaid
flowchart LR
    VA["Guest process virtual address"] --> GPT["Guest page tables: guest CR3"]
    GPT --> GPA["Guest physical address"]
    GPA --> NPT["Nested page tables: managed by KVM"]
    NPT --> HPA["Host physical RAM"]
```

### Switching between guest execution and host handling

The problem is not only running instructions quickly: a guest kernel expects
to control privileged CPU state. Hardware virtualization gives it its own
execution context and lets KVM decide which events require host intervention.

The host already has its own interrupt descriptor table (IDT), trap handlers
and syscall entry setup. The guest kernel creates its own equivalents and sets
its guest `IDTR` (the register locating the IDT), page-table root (`CR3`), and
other privileged state.
Virtualization keeps these contexts separate. A normal guest syscall or guest
page fault can be handled by the guest kernel; it need not bounce through QEMU.
An intercepted operation causes a **VM exit**, a hardware transition to KVM's
host handling, rather than adding a new host IDT entry for every VM. Some exits
are handled entirely in KVM; others return to QEMU for device handling.

```mermaid
sequenceDiagram
    participant Q as Host user mode: QEMU vCPU thread, UID libvirt-qemu
    participant K as Host kernel mode: KVM code
    participant C as Guest execution on that host thread: ring 3 or ring 0
    Q->>K: KVM_RUN request
    K->>C: Restore guest context, enter guest mode
    C->>C: Run guest application instructions
    C->>C: Guest syscall: run guest kernel, return to application
    C->>K: VM exit on an intercepted event
    alt Kernel can handle the exit
        K->>C: Handle and resume guest
    else Userland device handling is needed
        K->>Q: Return exit information
        Q->>Q: Service the virtual device operation
        Q->>K: Run again
        K->>C: Resume guest execution
    end
```

KVM's API represents VMs/vCPUs as file descriptors and exposes requests such as
`KVM_CREATE_VM`, memory registration and `KVM_RUN`. A saved XML definition is
not one of these runtime kernel objects. See [the KVM API](https://docs.kernel.org/virt/kvm/api.html).

### Where the CPU keeps this privileged state

Your Ryzen uses AMD SVM. In simplified terms, KVM manages:

| Mechanism | Purpose |
| --- | --- |
| `EFER.SVME` | Privileged CPU control enabling SVM execution |
| VMCB (Virtual Machine Control Block) | Kernel-managed memory describing guest CPU state, interception rules, interrupt controls and nested paging settings |
| `VM_HSAVE_PA` | Privileged register pointing to host state-save memory used for SVM transitions |
| `VMRUN` / VM exit | Enter guest execution; return to host handling on configured events |

These are managed per execution context/physical CPU as needed, not written
into a magic permanent CPU slot for each saved VM. Some state is held in CPU
registers while running, some in memory; hardware and KVM cooperate to save and
restore it. Intel provides analogous facilities with different names. See
[AMD's architecture manual, SVM chapter](https://docs.amd.com/api/khub/documents/sD1_QL~h4Afq2_tvzxqqSQ/content).

Direct instruction execution and hardware memory translation explain why CPU
work can be close to native. VM exits, scheduling and device I/O still have
costs; CPU-heavy, disk-heavy and network-heavy workloads need not have the same
overhead. The next section follows what happens when the guest requests I/O.

## 6. Devices turn guest requests into real I/O

Executing CPU instructions is only part of a computer. An OS also expects
registers, request queues and interrupts through which it can drive devices.
Virtualization must provide that contract and deliver the requested I/O.

| Approach | Problem it solves | How the guest reaches real hardware |
| --- | --- | --- |
| Emulated device | Run an existing driver expecting a conventional hardware model | QEMU implements that device's behavior in software |
| **Virtio** device | Avoid reproducing a complicated physical device when the guest supports a VM-specific driver | Guest and backend exchange requests through shared-memory queues and notifications |
| PCI passthrough | Give the guest direct control of a particular physical device | Assign the device through VFIO/IOMMU instead of emulating its main operations |

The guest discovers devices on its virtual PCI bus and loads the appropriate
drivers. With virtio, Linux knows the device is virtual; applications still get
normal disk and network interfaces. The backend is the host-side code servicing
those requests: QEMU for the disk path below, or potentially kernel code for
networking. Shared queues reduce handoffs; they do not make host work disappear.

### Storage: two filesystems with different owners

For a typical disk write:

```mermaid
flowchart LR
    app["Guest userland application: UID codex or root"] --> fs["Guest kernel: filesystem and block layer"]
    fs --> virtio["Guest kernel: virtio disk driver / queue"]
    virtio --> qemu["Host userland: QEMU block backend, UID libvirt-qemu"]
    qemu --> file["QCOW2 file on host filesystem"]
    file --> driver["Host kernel: filesystem / storage driver"]
    driver --> ssd["Physical SSD"]
```

The guest may format that disk without formatting the host SSD: it writes
filesystem structures into its virtual blocks, which become data inside the
image file. Reads/writes can be cached, batched or serviced asynchronously;
the diagram shows ownership, not every syscall. See
[QEMU's block formats](https://www.qemu.org/docs/master/system/qemu-block-drivers.html).

### Disposable storage without reinstalling everything

Recreating a fully provisioned disk each time would be slow and duplicate data.
A copy-on-write clone avoids that: the prepared base image stays unchanged.
Reads use the clone's changed blocks when present, otherwise the base; writes
go to the clone.
Deleting the clone loses its OS changes and downloads but leaves the base.
Do not remove a base still referenced by clones or modify an image in use.

### Networking: a NIC interface backed by software

The networking problem has two parts: give the guest a NIC it can drive, then
connect that NIC to other machines under host-controlled routing and policy.
The guest sees a **virtio NIC**, with its own MAC, IP and Linux interface. On
the host, a **TAP interface** carries Ethernet frames for that guest, and a
**software bridge** connects it to the host's virtual network. A bridge is a
software Ethernet switch; routing/NAT is a separate function. See
[Linux TAP](https://docs.kernel.org/networking/tuntap.html) and
[QEMU networking](https://www.qemu.org/docs/master/system/devices/net.html).

For guest internet traffic in the intended NAT setup:

```mermaid
flowchart LR
    subgraph guest["Guest OS"]
        socket["Guest userland: application / socket"] --> stack["Guest kernel: TCP/IP stack"]
        stack --> frontend["Guest kernel: virtio-net driver / queues"]
    end
    subgraph host["Host OS"]
        backend["Host kernel: vhost-net; or host userland: QEMU"] --> tap["Host kernel: guest TAP interface"]
        tap --> bridge["Host kernel: virbr-codex software bridge"]
        bridge --> routing["Host kernel: routing, firewall and NAT"]
        routing --> nic["Host kernel: NIC driver"]
    end
    frontend --> backend
    nic --> physical["Physical Wi-Fi or Ethernet NIC"]
    physical --> internet["Router / internet"]
```

Receive traffic follows the reverse logical path. Host routing selects the
actual internet connection; your direct MBP Ethernet cable need not carry
internet traffic. NAT rewrites guest source addresses to the host's outward
address and tracks the return traffic; the guest still keeps its private IP.

**Does this bypass QEMU or the CPU?** With `vhost-net`, much of the virtio network
data handling happens in the host kernel instead of QEMU userland. Shared
queues, batching and offloads reduce overhead. QEMU still configures the virtual
device, and host CPU/network-stack work remains. This is not a direct physical
NIC-to-guest connection. Copies and notifications depend on the implementation;
“virtio” does not mean guaranteed zero-copy. See
[vhost-net](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/virtualization_deployment_and_administration_guide/sect-network_configuration-disabling_vhost_net).

Passing through a whole physical NIC, or an SR-IOV virtual function on suitable
hardware, is a different arrangement that can bypass much of the host network
path. It is not the proposed setup: we want the host to retain its NIC and
control guest routing/firewall rules. Direct GPU assignment does not require
direct NIC assignment.

### Your MBP reaches the guest through the host

A private guest address does not need to be directly routed onto your Ethernet
link. The host keeps its existing `10.77.77.2` address. SSH `ProxyJump` first
connects to the host's SSH server, which opens a connection to the guest's
private IP. A second, end-to-end SSH session authenticates to the guest; the
client private key stays on the MBP. This path does not need guest internet NAT.

```mermaid
sequenceDiagram
    participant M as MBP userland: SSH client / Codex app
    participant H as Host userland: sshd at 10.77.77.2
    participant G as Guest userland: sshd at private IP
    M->>H: Authenticate to ai-box over direct Ethernet
    M->>H: Request TCP tunnel to guest:22
    H->>G: Open TCP connection through virtual network
    M->>G: Authenticate separately through tunnel
    Note over M,G: SSH is encrypted between MBP and guest, no private key copied
```

Both SSH servers are userland programs in their respective OSes. Their listener
normally starts as that OS's root account; per-connection processes separate
privileged handling from the authenticated user's session. The host connection
uses your host login account (for example `andy`); the guest connection uses
`codex`. Host sshd opens the forwarding connection through the host kernel's
network stack. Guest sshd starts the guest shell, and that shell starts Codex;
these are guest processes scheduled by the guest kernel onto QEMU's vCPUs.

Distinct simultaneous guests need distinct IP/MAC identities; replacements can
reuse an address after the old guest is removed. The lifecycle helper allocates
an unused IP/MAC slot and fresh instance identity for each new guest, including
clones. Use the address printed by `codex-vm create`; do not assume it is
`192.168.250.2`.

## 7. Choose ownership boundaries for the GPU and models

CPU, disk and network virtualization can share host resources. For the GPU,
our chosen tradeoff is different: dedicate the 5090 to the guest so it can use
its own NVIDIA driver and software stack.

**VFIO** is the host kernel framework used to assign the physical GPU to the
VM; the guest NVIDIA driver uses that GPU's interfaces. The host NVIDIA driver
cannot use it while assigned. The I/O memory management unit (IOMMU) maps the
device's memory accesses (DMA) to permitted host RAM; interrupt routing connects
device events to guest execution.
Much of the workload avoids emulating GPU operations, although setup, interrupt
handling and virtualization overhead remain. See
[VFIO](https://docs.kernel.org/driver-api/vfio.html).

Before applying it, check IOMMU groups, host display dependencies and GPU
reset behavior. Boot configuration may reserve the GPU for VFIO; whether it
returns to host use after guest shutdown depends on the chosen binding policy.
One passed-through GPU has one owning guest at a time.

Models have the opposite requirement: reuse existing data without giving the
guest ownership of the originals. A separate host-enforced file share exposes
all of `/mnt/data` read-only in the guest. This does not expose the
whole physical data SSD, and it must not merely be a guest-controlled read-only
mount. New downloads, packages and experiments go on the writable guest disk.
Export selected results before discard. A persistent disk that guest root can
write is persistent, but not protected from deletion by Codex.

## 8. Apply this model to dotfiles and everyday development

Dotfiles records how to prepare the host and build a guest. Those are separate
operations from starting an existing instance or discarding one. The intended
workflow is:

| Stage | Host effect | Developer experience |
| --- | --- | --- |
| Host preparation | Install QEMU/libvirt/tools; configure services, network/firewall and eventually VFIO | Approve host administration once; reboot if required |
| Base preparation | Download Ubuntu, boot/configure a guest, then shut down and retain a clean image | Wait for tools/drivers to install and verify GPU/isolation |
| Create an instance | Create a writable clone, define its identity/devices and start QEMU | Get a named SSH target and workspace |
| Work | Guest runs its own kernel, Codex and GPU tasks | Select guest in desktop app; authenticate Codex; allow full access inside it |
| Stop | Guest shuts down and QEMU exits; disks/definition remain | Resume later with its files intact |
| Discard/recreate | Remove the selected guest's definition and writable storage, then clone again | Start clean; original models and base survive |

Preparing the reusable base must clear instance-specific state so clones get
fresh machine IDs, SSH host keys and cloud-init instance state. Personal Codex
credentials should not be baked into that shared base. This keeps a reusable
installation separate from an individual machine's identity.

**Implemented in dotfiles:** opt-in `setup_codex_vm.sh` installs and configures
host infrastructure; it does not start a guest or reserve the GPU. The installed
`codex-vm` helper provides inspect, GPU prepare/unprepare, create, start, stop,
template and discard commands. Defaults are 8 vCPUs, 16 GiB RAM and a 100 GiB disk.
The guest receives both 5090 functions, a host-enforced read-only 9p export of
all `/mnt/data`, open NVIDIA drivers and guest-only full-access Codex settings.
Its SSH keys default to the host's existing plain `authorized_keys` entries.

`template` sanitizes a stopped clean guest into an independent read-only base;
`create --base` makes a writable clone with a fresh identity. `discard --yes`
requires a stopped managed guest and retains bases and host data. A CPU-only
builder can prepare software without taking the GPU. These are repository
implementations, not proof of successful runtime isolation or GPU performance;
[the operating guide](codex-vm.md) lists the remaining hardware checks.

The intended normal operation is to work **inside the guest**, not grant Codex
host administration each time. Host-side controls start, stop and reset it.
New guests need authentication; only explicitly retained data survives discard.
