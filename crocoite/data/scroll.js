/*	Continuously scrolls the page
 */
(function(){
let scrolled = new Map ();
let interval = null;
function stop() {
	window.clearInterval (interval);
	window.scrollTo (0, 0);
	scrolled.forEach (function (value, key, map) {
		key.scrollTop = value;
	});
}
/* save initial scroll state */
function save(obj) {
	if (!scrolled.has (obj)) {
		scrolled.set (obj, obj.scrollTop);
	}
}
/* perform a single scroll step */
function scroll (event) {
	window.scrollBy (0, window.innerHeight/2);
	document.querySelectorAll ('html body *').forEach (
		function (d) {
			if (d.scrollHeight-d.scrollTop > d.clientHeight) {
				save (d);
				d.scrollBy (0, d.clientHeight/2);
			}
		});
	return true;
}
interval = window.setInterval (scroll, 200);
return {'stop': stop};
}())
