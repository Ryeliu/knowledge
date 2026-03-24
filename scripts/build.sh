#!/bin/bash
# 用法：bash build.sh <tex文件路径>
# 例：bash build.sh /tmp/briefing_filled.tex

TEX_FILE="$1"
OUTPUT_DIR="$HOME/worklab/Sidejob/knowledge/output"

if [ -z "$TEX_FILE" ]; then
  echo "用法：bash build.sh <tex文件路径>"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

# 获取文件名（不含扩展名）
BASENAME=$(basename "$TEX_FILE" .tex)

# 编译两次（确保页眉页脚和目录正确）
xelatex -interaction=nonstopmode -output-directory="$OUTPUT_DIR" "$TEX_FILE"
xelatex -interaction=nonstopmode -output-directory="$OUTPUT_DIR" "$TEX_FILE"

# 清理辅助文件
rm -f "$OUTPUT_DIR/$BASENAME.aux" \
      "$OUTPUT_DIR/$BASENAME.log" \
      "$OUTPUT_DIR/$BASENAME.out" \
      "$OUTPUT_DIR/$BASENAME.toc"

echo "✅ PDF 已生成：$OUTPUT_DIR/$BASENAME.pdf"
