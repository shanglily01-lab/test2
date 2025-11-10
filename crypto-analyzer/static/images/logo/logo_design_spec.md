# AlphaFlow Logo 设计规范

## Logo SVG代码（简化版）

### 方案一：流动Alpha符号（推荐）

```svg
<svg width="200" height="200" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="alphaGradient" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2B6FED;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#7C3AED;stop-opacity:1" />
    </linearGradient>
  </defs>
  
  <!-- Alpha符号主体 -->
  <path d="M 50 150 Q 100 50, 150 150" 
        stroke="url(#alphaGradient)" 
        stroke-width="8" 
        fill="none" 
        stroke-linecap="round"/>
  
  <!-- 横线 -->
  <line x1="80" y1="100" x2="120" y2="100" 
        stroke="url(#alphaGradient)" 
        stroke-width="8" 
        stroke-linecap="round"/>
  
  <!-- 数据流线条1 -->
  <path d="M 20 80 Q 50 100, 50 150" 
        stroke="url(#alphaGradient)" 
        stroke-width="4" 
        fill="none" 
        opacity="0.6"
        stroke-linecap="round"/>
  
  <!-- 数据流线条2 -->
  <path d="M 150 150 Q 170 130, 180 100" 
        stroke="url(#alphaGradient)" 
        stroke-width="4" 
        fill="none" 
        opacity="0.6"
        stroke-linecap="round"/>
  
  <!-- 数据流线条3 -->
  <path d="M 100 20 Q 100 50, 100 100" 
        stroke="url(#alphaGradient)" 
        stroke-width="4" 
        fill="none" 
        opacity="0.6"
        stroke-linecap="round"/>
</svg>
```

### 方案二：极简AF字母组合

```svg
<svg width="200" height="200" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="afGradient" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2B6FED;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#7C3AED;stop-opacity:1" />
    </linearGradient>
  </defs>
  
  <!-- 字母A -->
  <path d="M 30 150 L 50 100 L 70 150 M 40 130 L 60 130" 
        stroke="url(#afGradient)" 
        stroke-width="10" 
        fill="none" 
        stroke-linecap="round" 
        stroke-linejoin="round"/>
  
  <!-- 连接线 -->
  <path d="M 70 150 Q 100 140, 130 150" 
        stroke="url(#afGradient)" 
        stroke-width="6" 
        fill="none" 
        opacity="0.5"
        stroke-linecap="round"/>
  
  <!-- 字母F -->
  <path d="M 130 100 L 130 150 M 130 100 L 170 100 M 130 125 L 160 125" 
        stroke="url(#afGradient)" 
        stroke-width="10" 
        fill="none" 
        stroke-linecap="round" 
        stroke-linejoin="round"/>
</svg>
```

## 使用说明

1. 将SVG代码保存为 `.svg` 文件
2. 可以使用在线工具（如 Figma、Adobe Illustrator）进一步优化
3. 导出不同尺寸：16x16, 32x32, 64x64, 128x128, 256x256
4. 生成PNG格式用于不同场景

## 设计工具推荐

- **Figma**：在线设计工具，免费版可用
- **Adobe Illustrator**：专业矢量图设计
- **Canva**：简单易用的设计平台
- **AI工具**：Midjourney, DALL-E 生成Logo概念图

