(() => {
  const ready = () => {
    if (document.getElementById('lightbox')) {
      return;
    }

    const lightbox = document.createElement('div');
    lightbox.className = 'lightbox';
    lightbox.id = 'lightbox';

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.id = 'lightbox-close';
    closeButton.textContent = 'Close';

    const image = document.createElement('img');
    image.alt = 'Expanded view';
    image.id = 'lightbox-image';

    const video = document.createElement('video');
    video.id = 'lightbox-video';
    video.controls = true;
    video.playsInline = true;
    video.muted = true;
    video.style.display = 'none';

    const caption = document.createElement('div');
    caption.className = 'lightbox-caption';
    caption.id = 'lightbox-caption';

    lightbox.append(closeButton, caption, image, video);
    document.body.appendChild(lightbox);

    const closeLightbox = () => {
      lightbox.classList.remove('is-open');
      image.src = '';
      image.style.display = 'none';
      video.pause();
      video.removeAttribute('src');
      video.style.display = 'none';
      caption.textContent = '';
    };

    document.querySelectorAll('[data-lightbox]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
        const src = trigger.getAttribute('data-lightbox');
        const text = trigger.getAttribute('data-lightbox-caption') || '';
        if (!src) return;
        image.src = src;
        image.style.display = 'block';
        video.style.display = 'none';
        caption.textContent = text;
        lightbox.classList.add('is-open');
      });
    });

    document.querySelectorAll('[data-lightbox-video]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
        const src = trigger.getAttribute('data-lightbox-video');
        const text = trigger.getAttribute('data-lightbox-caption') || '';
        if (!src) return;
        video.src = src;
        video.style.display = 'block';
        image.style.display = 'none';
        caption.textContent = text;
        lightbox.classList.add('is-open');
        video.play().catch(() => {});
      });
    });

    lightbox.addEventListener('click', (event) => {
      if (event.target === lightbox) {
        closeLightbox();
      }
    });

    closeButton.addEventListener('click', closeLightbox);

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeLightbox();
      }
    });
    document.querySelectorAll('[data-carousel]').forEach((carousel) => {
      const track = carousel.querySelector('[data-carousel-track]');
      if (!track) return;
      const slides = Array.from(track.querySelectorAll('[data-carousel-item]'));
      if (slides.length <= 1) return;

      const setActive = (activeIndex) => {
        slides.forEach((slide, idx) => {
          slide.classList.toggle('is-active', idx === activeIndex);
        });
      };

      const rotateToIndex = (targetIndex) => {
        const pivotIndex = Math.max(0, Math.min(slides.length - 1, targetIndex));
        const reordered = slides.slice(pivotIndex).concat(slides.slice(0, pivotIndex));
        reordered.forEach((slide) => track.appendChild(slide));
        slides.splice(0, slides.length, ...reordered);
        setActive(1);
      };

      slides.forEach((slide, idx) => {
        slide.addEventListener('click', () => {
          rotateToIndex(idx);
        });
      });

      setActive(1);
    });

    document.querySelectorAll('.nav-toggle').forEach((toggle) => {
      const nav = toggle.parentElement?.querySelector('.nav-links');
      if (!nav) return;
      toggle.addEventListener('click', () => {
        const isOpen = nav.classList.toggle('is-open');
        toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      });
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
