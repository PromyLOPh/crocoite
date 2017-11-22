/* Fixups for instagram: searches for the “show more” button and clicks it
 */
(function(){
function fixup () {
	var links = document.querySelectorAll ("main a"); 
	for (var i = 0; i < links.length; i++) { 
		var href = links[i].getAttribute ("href"); 
		if (href.search (/\?max_id=\d+$/) != -1) {
			var click = new MouseEvent('click', {
					view: window,
					bubbles: true,
					cancelable: true
					});
			console.log ('clicking', href);
			links[i].dispatchEvent (click);
			break;
		}
	}
}
window.addEventListener("load", fixup);
}());
