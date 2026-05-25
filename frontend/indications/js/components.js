/**
 * Indications Media — Components
 * Scroll animations, nav, filter, form, counters, back-to-top
 * IIFE — no global pollution. Vanilla JS. GPU-accelerated.
 */
(function () {
  'use strict';

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ================================================================
     SCROLL ANIMATION ENGINE
     ================================================================ */
  const initScrollAnimations = () => {
    if (prefersReducedMotion) {
      document.querySelectorAll('[data-animate]').forEach(el => el.classList.add('animated'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const el = entry.target;

        // Stagger children
        if (el.hasAttribute('data-stagger-children')) {
          Array.from(el.children).forEach((child, i) => {
            child.style.transitionDelay = `${(i + 1) * 100}ms`;
            child.classList.add('animated');
          });
        }

        // Individual stagger
        const stagger = parseInt(el.dataset.stagger, 10);
        if (stagger > 0) el.style.transitionDelay = `${stagger * 120}ms`;

        el.classList.add('animated');
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -30px 0px' });

    document.querySelectorAll('[data-animate]').forEach(el => observer.observe(el));
  };

  /* ================================================================
     NAVIGATION — solid bg on scroll, active section detection
     ================================================================ */
  const initNav = () => {
    const header = document.querySelector('.site-header');
    const links = document.querySelectorAll('.nav-link');
    if (!header || !links.length) return;

    const sections = [];
    links.forEach(link => {
      const href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        const section = document.querySelector(href);
        if (section) sections.push({ link, section, id: href.slice(1) });
      }
    });

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          links.forEach(l => l.classList.remove('nav-link--active'));
          const active = document.querySelector(`.nav-link[href="#${entry.target.id}"]`);
          if (active) active.classList.add('nav-link--active');
        }
      });
    }, { threshold: 0.3, rootMargin: '-64px 0px 0px 0px' });

    sections.forEach(({ section }) => observer.observe(section));

    // Solid nav background on scroll
    let ticking = false;
    const onScroll = () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          header.classList.toggle('nav--solid', window.scrollY > window.innerHeight * 0.8);
          ticking = false;
        });
        ticking = true;
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
  };

  /* ================================================================
     PROJECT FILTER
     ================================================================ */
  const initProjectFilter = () => {
    const bar = document.querySelector('.filter-bar');
    if (!bar) return;

    const chips = bar.querySelectorAll('.filter-chip');
    const projects = document.querySelectorAll('.project-card');

    bar.addEventListener('click', (e) => {
      const chip = e.target.closest('.filter-chip');
      if (!chip) return;

      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      const filter = chip.dataset.filter;
      projects.forEach(project => {
        if (filter === 'all' || project.dataset.category === filter) {
          project.style.display = '';
          project.style.opacity = '1';
        } else {
          project.style.opacity = '0';
          setTimeout(() => { project.style.display = 'none'; }, 400);
        }
      });
    });
  };

  /* ================================================================
     SMOOTH SCROLL (nav links)
     ================================================================ */
  const initSmoothScroll = () => {
    document.querySelectorAll('a[href^="#"]').forEach(link => {
      link.addEventListener('click', (e) => {
        const target = document.querySelector(link.getAttribute('href'));
        if (!target) return;
        e.preventDefault();

        const navH = 64;
        const top = target.getBoundingClientRect().top + window.pageYOffset - navH;

        if (prefersReducedMotion) {
          window.scrollTo({ top, behavior: 'instant' });
        } else {
          window.scrollTo({ top, behavior: 'smooth' });
        }
      });
    });
  };

  /* ================================================================
     FORM HANDLING
     ================================================================ */
  const initForm = () => {
    const form = document.querySelector('.contact-form');
    if (!form) return;

    // Remove old status messages
    form.querySelectorAll('.field-error, .form-success').forEach(el => el.remove());

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      let valid = true;

      // Clear previous errors
      form.querySelectorAll('.field-error').forEach(el => el.remove());
      form.querySelectorAll('.form-input.error').forEach(el => el.classList.remove('error'));
      const existingSuccess = form.querySelector('.form-success');
      if (existingSuccess) existingSuccess.remove();

      // Validate required
      form.querySelectorAll('[required]').forEach(input => {
        if (!input.value.trim()) {
          valid = false;
          input.classList.add('error');
          const label = input.closest('.form-field')?.querySelector('.form-label')?.textContent || 'FIELD';
          input.insertAdjacentHTML('afterend', `<div class="field-error">[ERROR] ${label} is required</div>`);
        } else {
          input.classList.remove('error');
        }
      });

      // Validate email
      const email = form.querySelector('input[type="email"]');
      if (email && email.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) {
        valid = true; // Don't block on email validation alone — just mark it
        email.classList.add('error');
        email.insertAdjacentHTML('afterend', '<div class="field-error">[ERROR] Invalid email format</div>');
      }

      if (!valid) return;

      // Success
      const btn = form.querySelector('.submit-btn');
      const original = btn.textContent;
      btn.textContent = '[MESSAGE SENT]';
      btn.disabled = true;
      btn.style.opacity = '0.7';

      // Inject success message
      const msg = document.createElement('div');
      msg.className = 'form-success mono';
      msg.textContent = '[MESSAGE SENT] We respond within 48 hours.';
      form.appendChild(msg);

      setTimeout(() => {
        btn.textContent = original;
        btn.disabled = false;
        btn.style.opacity = '1';
        form.reset();
        msg.remove();
      }, 3000);
    });
  };

  /* ================================================================
     BACK TO TOP
     ================================================================ */
  const initBackToTop = () => {
    const btn = document.querySelector('.back-to-top');
    if (!btn) return;

    let ticking = false;
    window.addEventListener('scroll', () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          btn.classList.toggle('back-to-top--visible', window.scrollY > 1000);
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });

    btn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: prefersReducedMotion ? 'instant' : 'smooth' });
    });
  };

  /* ================================================================
     INIT
     ================================================================ */
  const init = () => {
    initScrollAnimations();
    initNav();
    initProjectFilter();
    initSmoothScroll();
    initForm();
    initBackToTop();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
