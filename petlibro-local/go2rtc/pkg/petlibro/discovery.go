package petlibro

import (
	"encoding/binary"
	"fmt"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/tutk"
)

var discoveryCache sync.Map

func discoverByUID(conn *net.UDPConn, uid string, nonce []byte, subnets []string, verbose bool) (*net.UDPAddr, error) {
	cacheKey := discoveryCacheKey(uid, subnets)
	if cached, ok := discoveryCache.Load(cacheKey); ok {
		ip := net.ParseIP(cached.(string))
		if ip4 := ip.To4(); ip4 != nil {
			return &net.UDPAddr{IP: ip4, Port: lanPort}, nil
		}
	}

	req := tutk.TransCodePartial(nil, buildLANSearch3(uid, nonce, 1))
	targets := discoveryTargets(subnets)
	if len(targets) == 0 {
		return nil, fmt.Errorf("petlibro: no discovery targets")
	}

	if verbose {
		log.Debug().Msgf("discover uid=%s targets=%d sample=%s", uid, len(targets), sampleTargets(targets, 12))
	}

	buf := make([]byte, 65535)
	var sent, sendErrs, recv, ignored int
	var lastSendErr error
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		for _, target := range targets {
			if _, err := conn.WriteToUDP(req, target); err != nil {
				sendErrs++
				lastSendErr = err
			} else {
				sent++
			}
		}

		readUntil := time.Now().Add(250 * time.Millisecond)
		for time.Now().Before(readUntil) {
			_ = conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
			n, addr, err := conn.ReadFromUDP(buf)
			if err != nil || addr == nil || addr.IP.IsUnspecified() {
				continue
			}

			recv++
			pkt := tutk.ReverseTransCodePartial(nil, buf[:n])
			if len(pkt) < 12 || binary.LittleEndian.Uint16(pkt[8:]) != msgLANSearchR {
				ignored++
				continue
			}

			_ = conn.SetReadDeadline(time.Time{})
			cam := &net.UDPAddr{IP: append(net.IP(nil), addr.IP...), Port: lanPort}
			if verbose {
				log.Debug().Msgf("discover uid=%s found=%s response=%s sent=%d recv=%d ignored=%d", uid, cam, addr, sent, recv, ignored)
			}
			discoveryCache.Store(cacheKey, cam.IP.String())
			return cam, nil
		}
	}

	_ = conn.SetReadDeadline(time.Time{})
	if verbose {
		log.Debug().Msgf("discover uid=%s not found sent=%d send_errs=%d recv=%d ignored=%d last_send_err=%v", uid, sent, sendErrs, recv, ignored, lastSendErr)
	}
	if sent == 0 && lastSendErr != nil {
		return nil, fmt.Errorf("petlibro: discovery send failed: %w", lastSendErr)
	}
	return nil, fmt.Errorf("petlibro: camera with uid %s not found on LAN (targets=%d sample=%s sent=%d send_errs=%d recv=%d ignored=%d last_send_err=%v)",
		uid, len(targets), sampleTargets(targets, 12), sent, sendErrs, recv, ignored, lastSendErr)
}

func discoveryCacheKey(uid string, subnets []string) string {
	return uid + "|" + strings.Join(subnets, ",")
}

func clearDiscoveryCache(uid string, subnets []string) {
	discoveryCache.Delete(discoveryCacheKey(uid, subnets))
}

func discoveryTargets(subnets []string) []*net.UDPAddr {
	seen := map[string]struct{}{}
	var targets []*net.UDPAddr

	add := func(ip net.IP) {
		ip4 := ip.To4()
		if ip4 == nil || ip4.IsUnspecified() {
			return
		}
		key := net.JoinHostPort(ip4.String(), strconv.Itoa(lanPort))
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		targets = append(targets, &net.UDPAddr{IP: append(net.IP(nil), ip4...), Port: lanPort})
	}

	add(net.IPv4bcast)
	for _, subnet := range subnets {
		for _, host := range cidrDiscoveryTargets(subnet, 65536) {
			add(host)
		}
	}

	ifaces, err := net.Interfaces()
	if err != nil {
		return targets
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 ||
			iface.Flags&net.FlagBroadcast == 0 || iface.Flags&net.FlagPointToPoint != 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			ipNet, ok := addr.(*net.IPNet)
			if !ok {
				continue
			}
			ip := ipNet.IP.To4()
			mask := ipNet.Mask
			if ip == nil || len(mask) != net.IPv4len {
				continue
			}
			bcast := net.IPv4(ip[0]|^mask[0], ip[1]|^mask[1], ip[2]|^mask[2], ip[3]|^mask[3])
			add(bcast)
			for _, host := range subnetDiscoveryTargets(ip, mask, 1024) {
				add(host)
			}
		}
	}

	return targets
}

func cidrDiscoveryTargets(subnet string, max int) []net.IP {
	_, ipNet, err := net.ParseCIDR(subnet)
	if err != nil {
		return nil
	}
	ip := ipNet.IP.To4()
	mask := ipNet.Mask
	if ip == nil || len(mask) != net.IPv4len {
		return nil
	}
	return subnetDiscoveryTargets(ip, mask, max)
}

func subnetDiscoveryTargets(ip net.IP, mask net.IPMask, max int) []net.IP {
	ones, bits := mask.Size()
	if bits != 8*net.IPv4len {
		return nil
	}

	total := 1 << (bits - ones)
	if total > max {
		return nil
	}

	base := binary.BigEndian.Uint32(ip) & binary.BigEndian.Uint32(mask)
	var targets []net.IP
	for i := uint32(1); i < uint32(total-1); i++ {
		host := make(net.IP, net.IPv4len)
		binary.BigEndian.PutUint32(host, base+i)
		if host.Equal(ip) {
			continue
		}
		targets = append(targets, host)
	}
	return targets
}

func sampleTargets(targets []*net.UDPAddr, max int) string {
	if len(targets) == 0 {
		return ""
	}
	if len(targets) < max {
		max = len(targets)
	}
	s := make([]string, 0, max)
	for _, target := range targets[:max] {
		s = append(s, target.String())
	}
	if len(targets) > max {
		s = append(s, fmt.Sprintf("...+%d", len(targets)-max))
	}
	return strings.Join(s, ",")
}
