package main

import (
	"crypto/sha1"
	"encoding/hex"
)

// shortSHA1 返回 data_dir 派生摘要（sha1 前 24 位 hex），启动器互斥量名的一部分。
// 命名如实使用 sha1（P2-10：原先函数名写 SHA256 实际用 sha1，误导）。
func shortSHA1(s string) string {
	sum := sha1.Sum([]byte(s))
	h := hex.EncodeToString(sum[:])
	if len(h) > 24 {
		return h[:24]
	}
	return h
}