/*	Continuously scrolls the page
 */
(function(){
class Scroll {
	constructor (options) {
		this.scrolled = new Map ();
		this.interval = window.setInterval (this.scroll.bind (this), 200);
	}

	stop() {
		window.clearInterval (this.interval);
		window.scrollTo (0, 0);
		this.scrolled.forEach (function (value, key, map) {
			key.scrollTop = value;
		});
	}
	/* save initial scroll state */
	save(obj) {
		if (!this.scrolled.has (obj)) {
			this.scrolled.set (obj, obj.scrollTop);
		}
	}
	/* perform a single scroll step */
	scroll (event) {
		window.scrollBy (0, window.innerHeight/2);
		document.querySelectorAll ('html body *').forEach (
			function (d) {
				if (d.scrollHeight-d.scrollTop > d.clientHeight) {
					this.save (d);
					d.scrollBy (0, d.clientHeight/2);
				}
			}.bind (this));
		return true;
	}
}

return Scroll;
}())
