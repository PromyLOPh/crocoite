(function(){
function scroll (event) {
	if (__crocoite_stop__) {
		return false;
	} else {
		window.scrollBy (0, window.innerHeight/2);
		return true;
	}
}
function onload (event) {
    window.setInterval (scroll, 200);
}
document.addEventListener("DOMContentLoaded", onload);
}());
