/** 图片附件管线：File → {media_type, base64}（票 02）；降采样在后续循环加入。 */

export interface ImageAttachment {
  media_type: string
  data: string
  name: string
}

export const IMAGE_TYPES = /^image\/(png|jpeg|webp|gif)$/
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_IMAGES = 4

/** File → {media_type, base64}；长边超过 2000px 时先降采样（gif 除外，canvas 会丢动画）。 */
export async function fileToImageBlock(file: File): Promise<{ media_type: string; data: string }> {
  if (
    file.type !== 'image/gif' &&
    typeof createImageBitmap === 'function' &&
    typeof document !== 'undefined'
  ) {
    try {
      const bitmap = await createImageBitmap(file)
      const target = computeTargetSize(bitmap.width, bitmap.height)
      if (target) {
        const canvas = document.createElement('canvas')
        canvas.width = target.width
        canvas.height = target.height
        const ctx = canvas.getContext('2d')
        if (ctx) {
          ctx.drawImage(bitmap, 0, 0, target.width, target.height)
          bitmap.close()
          const url = canvas.toDataURL(file.type === 'image/jpeg' ? 'image/jpeg' : 'image/png')
          const match = /^data:([^;]+);base64,(.*)$/.exec(url)
          if (match) return { media_type: match[1]!, data: match[2]! }
        }
      }
      bitmap.close()
    } catch {
      /* 降采样失败：回落原始 base64 管线 */
    }
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const match = /^data:([^;]+);base64,(.*)$/.exec(String(reader.result))
      if (!match) {
        reject(new Error('无法读取图片数据'))
        return
      }
      resolve({ media_type: match[1]!, data: match[2]! })
    }
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })
}

/** 降采样目标尺寸：长边超过 maxEdge 时等比缩到 maxEdge；无需缩放返回 null。 */
export function computeTargetSize(
  width: number,
  height: number,
  maxEdge = 2000,
): { width: number; height: number } | null {
  const long = Math.max(width, height)
  if (long <= maxEdge || long === 0) return null
  const ratio = maxEdge / long
  return { width: Math.round(width * ratio), height: Math.round(height * ratio) }
}
