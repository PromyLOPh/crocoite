/*	Generic clicking
 *
 *  We can’t just click every clickable object, since there may be side-effects
 *  like navigating to a different location. Thus whitelist known elements.
 */

(function() {
/*	Element is visible if itself and all of its parents are
 */
function isVisible (o) {
	if (o === null || !(o instanceof Element)) {
		return true;
	}
	let style = window.getComputedStyle (o);
	if ('parentNode' in o) {
		return style.display !== 'none' && isVisible (o.parentNode);
	} else {
		return style.display !== 'none';
	}
}

/*	Elements are considered clickable if they are a) visible and b) not
 *	disabled
 */
function isClickable (o) {
	return !o.hasAttribute ('disabled') && isVisible (o);
}

const defaultClickThrottle = 50; /* in ms */
const discoverInterval = 1000; /* 1 second */

class Click {
	constructor(options) {
		/* pick selectors matching current location */
		let hostname = document.location.hostname;
		this.selector = [];
		for (let s of options['sites']) {
			let r = new RegExp (s.match, 'i');
			if (r.test (hostname)) {
				this.selector = this.selector.concat (s.selector);
			}
		}
		/* throttle clicking */
		this.queue = [];
		this.clickTimeout = null;

		/* some sites don’t remove/replace the element immediately, so keep track of
		 * which ones we already clicked */
		this.have = new Set ();

		/* XXX: can we use a mutation observer instead? */
		this.interval = window.setInterval (this.discover.bind (this), discoverInterval);
	}

	makeClickEvent () {
		return new MouseEvent('click', {
					view: window,
					bubbles: true,
					cancelable: true
					});
	}

	click () {
		if (this.queue.length > 0) {
			const item = this.queue.shift ();
			const o = item.o;
			const selector = item.selector;
			o.dispatchEvent (this.makeClickEvent ());

			if (this.queue.length > 0) {
				const nextTimeout = 'throttle' in selector ?
						selector.throttle : defaultClickThrottle;
				this.clickTimeout = window.setTimeout (this.click.bind (this), nextTimeout);
			} else {
				this.clickTimeout = null;
			}
		}
	}

	discover () {
		for (let s of this.selector) {
			let obj = document.querySelectorAll (s.selector);
			for (let o of obj) {
				if (!this.have.has (o) && isClickable (o)) {
					this.queue.push ({o: o, selector: s});
					if (!s.multi) {
						this.have.add (o);
					}
				}
			}
		}
		if (this.queue.length > 0 && this.clickTimeout === null) {
			/* start clicking immediately */
			this.clickTimeout = window.setTimeout (this.click.bind (this), 0);
		}
		return true;
	}


	stop () {
		window.clearInterval (this.interval);
		window.clearTimeout (this.clickTimeout);
	}
}

return Click;
}())
