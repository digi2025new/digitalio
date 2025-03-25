document.addEventListener('DOMContentLoaded', () => {
    // Modal functions for the public page
    window.openModal = function(url, filetype) {
      const modal = document.getElementById('modal');
      const modalContent = document.getElementById('modalContent');
      // Clear previous content
      modalContent.innerHTML = "";
      
      // Based on filetype, create the appropriate element
      if(['png', 'jpg', 'jpeg', 'gif'].includes(filetype)) {
        const img = document.createElement('img');
        img.src = url;
        img.alt = "Notice Image";
        modalContent.appendChild(img);
      } else if(filetype === 'mp4') {
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        modalContent.appendChild(video);
      } else if(filetype === 'mp3') {
        const audio = document.createElement('audio');
        audio.src = url;
        audio.controls = true;
        audio.autoplay = true;
        modalContent.appendChild(audio);
      } else {
        const link = document.createElement('a');
        link.href = url;
        link.target = "_blank";
        link.textContent = "View Document";
        modalContent.appendChild(link);
      }
      
      // Display the modal
      modal.style.display = "block";
    };
  
    window.closeModal = function() {
      const modal = document.getElementById('modal');
      modal.style.display = "none";
    };
  });
  