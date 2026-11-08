/** Product photo or a package icon. Lazy, async-decoded and fixed-size, so it never shifts the layout or blocks input. */
import { memo, useState } from 'react'
import { Icon } from './Icon'

export const ProductThumb = memo(function ProductThumb({ src, large }: { src?: string | null; large?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null)
  const show = src && failed !== src
  return (
    <span className={`prod-thumb${large ? ' lg' : ''}${show ? ' has-img' : ''}`}>
      {show ? (
        <img src={src} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setFailed(src)} />
      ) : (
        <Icon name="package" size={large ? 26 : 18} />
      )}
    </span>
  )
})
