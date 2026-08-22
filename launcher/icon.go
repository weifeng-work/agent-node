package main

import (
	_ "embed"
)

// ---- 托盘图标：内嵌多尺寸 .ico（由 gen_ico.py 生成），深色任务栏下清晰可见 ----
//
// 图标为"靶心"设计：亮色中心点 + 白色环 + 深色外环，绿/红分别代表健康/异常。
// 生成方式：python gen_ico.py（依赖 Pillow，仅开发期使用，产物 icon-*.ico 提交入库）。

//go:embed icon-green.ico
var iconGreenIco []byte

//go:embed icon-red.ico
var iconRedIco []byte

func iconGreen() []byte { return iconGreenIco }
func iconRed() []byte   { return iconRedIco }