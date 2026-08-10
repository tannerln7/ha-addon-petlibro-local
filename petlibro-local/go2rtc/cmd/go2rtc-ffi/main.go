package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"sync"
	"unsafe"

	"github.com/AlexxIT/go2rtc/internal/embedded"
)

var (
	errMu   sync.Mutex
	lastErr string
)

func setErr(err error) C.int {
	errMu.Lock()
	defer errMu.Unlock()

	if err == nil {
		lastErr = ""
		return 0
	}
	lastErr = err.Error()
	return -1
}

//export Go2RTCStart
func Go2RTCStart(configYAML *C.char) C.int {
	var cfg string
	if configYAML != nil {
		cfg = C.GoString(configYAML)
	}
	return setErr(embedded.Start(cfg))
}

//export Go2RTCAddStream
func Go2RTCAddStream(name *C.char, source *C.char) C.int {
	if name == nil || source == nil {
		return setErr(embedded.AddStream("", ""))
	}
	return setErr(embedded.AddStream(C.GoString(name), C.GoString(source)))
}

//export Go2RTCDeleteStream
func Go2RTCDeleteStream(name *C.char) C.int {
	if name == nil {
		return setErr(embedded.DeleteStream(""))
	}
	return setErr(embedded.DeleteStream(C.GoString(name)))
}

//export Go2RTCLastError
func Go2RTCLastError() *C.char {
	errMu.Lock()
	defer errMu.Unlock()
	return C.CString(lastErr)
}

//export Go2RTCFreeString
func Go2RTCFreeString(s *C.char) {
	C.free(unsafe.Pointer(s))
}

func main() {}
