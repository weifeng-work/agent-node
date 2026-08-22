package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
)

// ---- 托盘图标：运行时生成绿/红实心圆 PNG（避免携带二进制资产） ----

func drawIcon(base color.RGBA) []byte {
	const s = 32
	img := image.NewRGBA(image.Rect(0, 0, s, s))
	// 透明底
	for y := 0; y < s; y++ {
		for x := 0; x < s; x++ {
			img.Set(x, y, color.RGBA{0, 0, 0, 0})
		}
	}
	// 外圈描边 + 内填充圆
	drawCircle(img, s/2, s/2, float64(s/2-2), color.RGBA{R: 60, G: 60, B: 60, A: 255})
	drawCircle(img, s/2, s/2, float64(s/2-4), base)

	var buf bytes.Buffer
	_ = png.Encode(&buf, img)
	return buf.Bytes()
}

func drawCircle(img *image.RGBA, cx, cy int, r float64, c color.RGBA) {
	for y := cy - int(r); y <= cy+int(r); y++ {
		for x := cx - int(r); x <= cx+int(r); x++ {
			if x < 0 || y < 0 || x >= img.Bounds().Dx() || y >= img.Bounds().Dy() {
				continue
			}
			dx := float64(x-cx)
			dy := float64(y-cy)
			if dx*dx+dy*dy <= r*r {
				img.Set(x, y, c)
			}
		}
	}
}

func iconGreen() []byte { return drawIcon(color.RGBA{R: 46, G: 200, B: 74, A: 255}) }
func iconRed() []byte   { return drawIcon(color.RGBA{R: 220, G: 60, B: 60, A: 255}) }