// The launch animation's scene (SVG), designed in mocks/launch-animation.html. Static markup; animated by LaunchAnimation.tsx.
export const SCENE_SVG = `<svg id="scene" role="img" viewBox="-320 -180 640 360" preserveAspectRatio="xMidYMid slice" aria-label="Featherbox launch animation in clay: a clay feather twirls down onto a sad clay laptop, is drawn into its screen, and the laptop comes alive with the logo">
    <defs>
      <linearGradient id="bgGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff8a2a"/><stop offset=".55" stop-color="#ff5a52"/><stop offset="1" stop-color="#ff3d7f"/></linearGradient>
      <radialGradient id="keyLight" cx=".32" cy=".18" r=".75"><stop offset="0" stop-color="#fff" stop-opacity=".30"/><stop offset=".5" stop-color="#fff" stop-opacity=".06"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <radialGradient id="vignette" cx=".5" cy=".5" r=".75"><stop offset=".6" stop-color="#7a0f3a" stop-opacity="0"/><stop offset="1" stop-color="#7a0f3a" stop-opacity=".36"/></radialGradient>
      <radialGradient id="aura"><stop offset="0" stop-color="#fff" stop-opacity=".6"/><stop offset=".45" stop-color="#fff" stop-opacity=".15"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <filter id="backdropGrain" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency=".75" numOctaves="2" seed="4" result="n"/>
        <feColorMatrix in="n" type="matrix" values="0 0 0 0 .45  0 0 0 0 .08  0 0 0 0 .2  0 0 0 -.9 .55"/>
        <feComposite in2="SourceGraphic" operator="in"/>
      </filter>
      <filter id="blobShadow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5"/></filter>
      <filter id="clay" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">
        <feTurbulence class="boil" type="fractalNoise" baseFrequency=".045" numOctaves="2" seed="1" result="noise"/>
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.4" xChannelSelector="R" yChannelSelector="G" result="shape"/>
        <feColorMatrix in="shape" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="alpha"/>
        <feGaussianBlur in="alpha" stdDeviation="2.4" result="soft"/>
        <feOffset in="soft" dx="-2" dy="-2.6" result="upLeft"/>
        <feComposite in="alpha" in2="upLeft" operator="arithmetic" k2="1" k3="-1" result="shadowRim"/>
        <feFlood flood-color="#4a0a2a" flood-opacity=".42"/><feComposite in2="shadowRim" operator="in" result="shade"/>
        <feOffset in="soft" dx="1.8" dy="2.4" result="downRight"/>
        <feComposite in="alpha" in2="downRight" operator="arithmetic" k2="1" k3="-1" result="lightRim"/>
        <feFlood flood-color="#fff" flood-opacity=".6"/><feComposite in2="lightRim" operator="in" result="light"/>
        <feTurbulence type="fractalNoise" baseFrequency="1.1" numOctaves="1" seed="9" result="grainN"/>
        <feColorMatrix in="grainN" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.4 .7" result="grainA"/>
        <feComposite in="grainA" in2="alpha" operator="in" result="grainIn"/>
        <feFlood flood-color="#2a0718" flood-opacity=".22"/><feComposite in2="grainIn" operator="in" result="grain"/>
        <feMerge><feMergeNode in="shape"/><feMergeNode in="grain"/><feMergeNode in="shade"/><feMergeNode in="light"/></feMerge>
      </filter>
      <filter id="claySmall" x="-40%" y="-40%" width="180%" height="180%" color-interpolation-filters="sRGB">
        <feTurbulence class="boil" type="fractalNoise" baseFrequency=".09" numOctaves="1" seed="1" result="noise"/>
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="1.3" xChannelSelector="R" yChannelSelector="G" result="shape"/>
        <feColorMatrix in="shape" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="alpha"/>
        <feGaussianBlur in="alpha" stdDeviation="1" result="soft"/>
        <feOffset in="soft" dx="-.9" dy="-1.2" result="upLeft"/>
        <feComposite in="alpha" in2="upLeft" operator="arithmetic" k2="1" k3="-1" result="shadowRim"/>
        <feFlood flood-color="#2a0718" flood-opacity=".45"/><feComposite in2="shadowRim" operator="in" result="shade"/>
        <feOffset in="soft" dx=".8" dy="1.1" result="downRight"/>
        <feComposite in="alpha" in2="downRight" operator="arithmetic" k2="1" k3="-1" result="lightRim"/>
        <feFlood flood-color="#fff" flood-opacity=".55"/><feComposite in2="lightRim" operator="in" result="light"/>
        <feMerge><feMergeNode in="shape"/><feMergeNode in="shade"/><feMergeNode in="light"/></feMerge>
      </filter>
      <filter id="clayText" x="-10%" y="-30%" width="120%" height="160%" color-interpolation-filters="sRGB">
        <feTurbulence class="boil" type="fractalNoise" baseFrequency=".08" numOctaves="1" seed="1" result="noise"/>
        <feDisplacementMap in="SourceGraphic" in2="noise" scale=".9" xChannelSelector="R" yChannelSelector="G" result="shape"/>
        <feColorMatrix in="shape" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="alpha"/>
        <feGaussianBlur in="alpha" stdDeviation=".7" result="soft"/>
        <feOffset in="soft" dx="-.5" dy="-.8" result="upLeft"/>
        <feComposite in="alpha" in2="upLeft" operator="arithmetic" k2="1" k3="-1" result="shadowRim"/>
        <feFlood flood-color="#2a0718" flood-opacity=".5"/><feComposite in2="shadowRim" operator="in" result="shade"/>
        <feOffset in="soft" dx=".4" dy=".7" result="downRight"/>
        <feComposite in="alpha" in2="downRight" operator="arithmetic" k2="1" k3="-1" result="lightRim"/>
        <feFlood flood-color="#ffd9c0" flood-opacity=".55"/><feComposite in2="lightRim" operator="in" result="light"/>
        <feMerge><feMergeNode in="shape"/><feMergeNode in="shade"/><feMergeNode in="light"/></feMerge>
      </filter>
      <linearGradient id="logoGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff7a1a"/><stop offset="1" stop-color="#ff3d7f"/></linearGradient>
      <linearGradient id="rimGrad" gradientUnits="userSpaceOnUse" x1="-50" y1="-20" x2="50" y2="60"><stop offset="0" stop-color="#ffb066"/><stop offset="1" stop-color="#ff5c9a"/></linearGradient>
      <linearGradient id="titleEmber" gradientUnits="userSpaceOnUse" x1="-72" y1="0" x2="72" y2="0"><stop offset="0" stop-color="#4a0d2c"/><stop offset=".5" stop-color="#7d1a2a"/><stop offset="1" stop-color="#b8400f"/></linearGradient>
      <clipPath id="screenClip"><rect x="-37" y="-5" width="74" height="50" rx="8"/></clipPath>
      <symbol id="clayFeather" viewBox="0 0 24 24" overflow="visible">
        <path d="M12.67 19a2 2 0 0 0 1.416-.588l6.154-6.172a6 6 0 0 0-8.49-8.49L5.586 9.914A2 2 0 0 0 5 11.328V18a1 1 0 0 0 1 1z" fill="#fff8f2" stroke="#fff8f2" stroke-width="1.6" stroke-linejoin="round"/>
        <path d="M16 8 2 22" fill="none" stroke="#ffd2b0" stroke-width="2.1" stroke-linecap="round"/>
        <path d="M17.5 15H9" fill="none" stroke="#ffd2b0" stroke-width="1.6" stroke-linecap="round"/>
      </symbol>
    </defs>

    <rect class="bgfill" fill="url(#bgGrad)"/>
    <rect class="bgfill" fill="url(#keyLight)"/>
    <rect class="bgfill" fill="#fff" filter="url(#backdropGrain)" opacity=".5"/>
    <rect class="bgfill" fill="url(#vignette)"/>
    <g id="art">
      <circle id="auraC" cx="0" cy="22" r="130" fill="url(#aura)" opacity="0"/>
      <ellipse id="shadow" cx="0" cy="72" rx="64" ry="8" fill="#6e0d34" opacity=".4" filter="url(#blobShadow)"/>
      <g id="laptop">
        <g id="base" filter="url(#clay)">
          <path id="baseP" d="M-52,50 H52 Q58,50 60,55 L63,62 Q64,68 58,68 H-58 Q-64,68 -63,62 L-60,55 Q-58,50 -52,50 Z" fill="#b2b1b8"/>
          <rect id="padP" x="-13" y="58" width="26" height="5" rx="2.5" fill="#9b9aa2"/>
        </g>
        <g id="lid">
          <g filter="url(#clay)">
            <rect id="lidP" x="-46" y="-14" width="92" height="66" rx="13" fill="#a7a6ae"/>
            <rect id="rimP" x="-46" y="-14" width="92" height="66" rx="13" fill="none" stroke="url(#rimGrad)" stroke-width="3.2" opacity="0"/>
            <rect id="screenP" x="-37" y="-5" width="74" height="50" rx="8" fill="#6f7079"/>
          </g>
          <g clip-path="url(#screenClip)">
            <rect id="screenFlash" x="-37" y="-5" width="74" height="50" fill="#fff4ea" opacity="0"/>
          </g>
          <path id="crack" d="M24,-5 L19,4 L23,9 L16,19" fill="none" stroke="#5a5b63" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" filter="url(#claySmall)"/>
          <g id="face" filter="url(#claySmall)">
            <path id="browL" d="M-21,9 L-10,6" stroke="#3d3e45" stroke-width="3" stroke-linecap="round" fill="none"/>
            <path id="browR" d="M21,9 L10,6" stroke="#3d3e45" stroke-width="3" stroke-linecap="round" fill="none"/>
            <ellipse id="eyeL" cx="-14" cy="18" rx="4.2" ry="3.4" fill="#3d3e45"/>
            <ellipse id="eyeR" cx="14" cy="18" rx="4.2" ry="3.4" fill="#3d3e45"/>
            <path id="lidL" d="M-19,15.5 Q-14,13 -9,15.5" stroke="#6f7079" stroke-width="4.5" stroke-linecap="round" fill="none"/>
            <path id="lidR" d="M19,15.5 Q14,13 9,15.5" stroke="#6f7079" stroke-width="4.5" stroke-linecap="round" fill="none"/>
            <path id="mouth" d="M-9,34 Q0,28 9,34" stroke="#3d3e45" stroke-width="3.2" stroke-linecap="round" fill="none"/>
            <ellipse id="tear" cx="-15" cy="25" rx="2" ry="2.8" fill="#8fc3ec"/>
          </g>
          <g id="logoOn" opacity="0"><g id="logoScale" filter="url(#clay)">
            <rect x="-15" y="-15" width="30" height="30" rx="9" fill="url(#logoGrad)"/>
            <use href="#clayFeather" x="-10" y="-10" width="20" height="20"/>
          </g></g>
        </g>
      </g>

      <circle id="ring" cx="0" cy="20" r="6" fill="none" stroke="#fff" opacity="0"/>
      <g id="blobs"></g>
      <g id="featherG"><g id="featherSpin"><g filter="url(#clay)"><use id="featherUse" href="#clayFeather" x="-24" y="-24" width="48" height="48" transform="rotate(45)"/></g></g></g>

      <g id="word" opacity="0" font-family="Segoe UI Variable Display, Segoe UI, system-ui, sans-serif" text-anchor="middle">
        <text x="0" y="115" font-size="28" font-weight="800" letter-spacing="-.4" fill="url(#titleEmber)" filter="url(#clayText)">Featherbox</text>
        <text x="0" y="134" font-size="11" font-weight="600" letter-spacing="1.6" fill="#6b1736" fill-opacity=".8">LOCAL AI, LIGHT ON YOUR PC</text>
      </g>
    </g>
  </svg>`;
