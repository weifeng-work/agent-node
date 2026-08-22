package main

import (
	"os"
	"testing"
)

func TestEmbeddedIconsMatchDisk(t *testing.T) {
	gd, _ := os.ReadFile("icon-green.ico")
	rd, _ := os.ReadFile("icon-red.ico")
	if len(gd) == 0 || len(rd) == 0 {
		t.Fatal("disk icons missing")
	}
	if string(iconGreenIco) != string(gd) {
		t.Fatalf("green embed mismatch: disk=%d embed=%d", len(gd), len(iconGreenIco))
	}
	if string(iconRedIco) != string(rd) {
		t.Fatalf("red embed mismatch: disk=%d embed=%d", len(rd), len(iconRedIco))
	}
}
