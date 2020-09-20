// Call an A/C get API command and return an array containing the time taken for
// the response to be received and the reponse values in a map
// or throw an error if one is raised
var aircon_api_get = function(host, path) {
  var url = host + path
  var req_time = new Date().getTime();
  console.debug(url);
  return fetch(url)
  .then(function(response) {
    if (!response.ok) {
      throw new Error('Url "' + url + '" returned ' + response.statusText);
    }
    return response.text()
  })
  .then(function(text) {
    var sec_elapsed = (new Date().getTime() - req_time) / 1000
    return [sec_elapsed, text.split(',').reduce(function(map, obj) {
      [key, value] = obj.split('=');
      if (! ['ret', 'err'].includes(key)) {
        map[key] = value;
      }
      return map
    }, {})]
  })
  .catch(function(err) {
    console.error('Fetch Error url("' + '/' + host + path + '"):', err)
    throw err;
  })
}

// Call an A/C set API command with the set parameters in the dict parameter to
// be sent encoded as HTTP GET parameters and return an array containing the time
// taken for the response to be received and the reponse values in a map
// or throw an error if one is raised
var aircon_api_set = function(host, path, dict) {
  var url = host + path + '?' + new URLSearchParams(dict).toString();
  console.info(url);
  return fetch(url)
  .then(function(response) {
    if (!response.ok) {
      throw new Error('Url "' + url + '" returned ' + response.statusText);
    }
    return response.text();
  })
  .then(function(text) {
    var dict = text.split(',').reduce(function(map, obj) {
      [key, value] = obj.split('=');
      map[key] = value;
      return map;
    }, {});
    if (dict.ret == 'OK') {
      return dict;
    } else {
      throw new Error('aircon_api_set returned with ' + text);
    }
  })
  .catch(function(err) {
    console.error('Fetch Error: ', err)
    throw err;
  })
}

// Style the pressed button to make it look selected
// only for a short period of time (plus and minus buttons)
function press_button() {
  var elem = document.activeElement;
  elem.classList.add("active");
  setTimeout(() => {
    elem.classList.remove("active");
    elem.blur();
  }, 200)
}

// Each controlled A/C unit. It could have been made as a Vue component
// but did not really have to, so instead a single Vue application controls all
// of the defined Aircon objects
class Aircon {
  constructor(num, host) {
    // Just an index
    this.num = num;

    // Default name, it will be overriden by the one set in the wifi controller
    this.name = 'aircon' + num;

    // Path to communicate with the wifi controller API
    this.host = host;

    // API values are stored in the sensors, info and controls dictionaries
    this.sensors = {};
    this.info = {};
    this.controls = {};

    // Use this to mark this.controls as dirty i.e. some values need to be set
    // on the controller. When this is done, a poll timer also needs to be reset
    // to give the user more time to press additional buttons
    // before the command is sent
    this.command_to_send = false;
  }

  // Change the selected button for the aircon mode
  display_mode() {
    setTimeout(() => document.activeElement.blur(), 200);
    for (var x of document.getElementById('mode' + this.num).getElementsByTagName('button')) {
      x.classList.remove('btn-dark', 'active');
      x.classList.add('btn-outline-dark');
    }
    var cl;
    switch (this.controls.mode) {
      // Auto mode is weird. We get told we're on auto by mode 0
      // but we need to set mode 1 or 7 to request auto, yey!
      // let's not talk about the humidify mode, we cannot set it using the api and
      // it's hard to come out of it because in it, last heating temperature is set to --
      case '':  cl = document.getElementById('humidify' + this.num).classList; break;
      case '0': cl = document.getElementById('auto' + this.num).classList; break;
      case '1': cl = document.getElementById('auto' + this.num).classList; break;
      case '2': cl = document.getElementById('dry' + this.num).classList; break;
      case '3': cl = document.getElementById('cool' + this.num).classList; break;
      case '4': cl = document.getElementById('heat' + this.num).classList; break;
      case '6': cl = document.getElementById('fanonly' + this.num).classList; break;
      case '7': cl = document.getElementById('auto' + this.num).classList; break;
    }
    if (cl) {
      cl.remove('btn-outline-dark');
      cl.add('btn-dark', 'active');
    }
  }

  // Mark the OFF button as selected
  display_off() {
    setTimeout(() => document.activeElement.blur(), 200);
    document.getElementById('on' + this.num).classList.remove('active', 'btn-success');
    document.getElementById('on' + this.num).classList.add('btn-outline-success');
    document.getElementById('off' + this.num).classList.remove('btn-outline-danger');
    document.getElementById('off' + this.num).classList.add('active', 'btn-danger');
  }

  // Mark the ON button as selected
  display_on() {
    setTimeout(() => document.activeElement.blur(), 200);
    document.getElementById('off' + this.num).classList.remove('active', 'btn-danger');
    document.getElementById('off' + this.num).classList.add('btn-outline-danger');
    document.getElementById('on' + this.num).classList.remove('btn-outline-success');
    document.getElementById('on' + this.num).classList.add('active', 'btn-success');
  }

  // Called when the OFF button is pressed
  set_off() {
    this.display_off();
    this.controls.pow = 0;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the ON button is pressed
  set_on() {
    this.display_on();
    this.controls.pow = 1;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the set temperature minus button is pressed
  stemp_minus() {
    press_button();
    var mode = this.controls.mode;
    if ((mode == 4 && this.controls.stemp > 10) ||
        ((mode == 0 || mode == 1 || mode == 3 || mode == 7) && this.controls.stemp > 18))
    {
      this.controls.stemp--;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when the set temperature plus button is pressed
  stemp_plus() {
    press_button();
    var mode = this.controls.mode;
    if ((mode == 4 && this.controls.stemp < 30) ||
        ((mode == 0 || mode == 1 || mode == 3 || mode == 7) && this.controls.stemp < 32))
    {
      this.controls.stemp++;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when temperature or humidity text value is selected for an edit
  edit_focus() {
    // Stop the poll timer so the display does not bump
    // it may take several seconds for the user to change the value
    app.stop_timer();
  }

  // Called when temperature or humidity text value is not edited anymore
  // typically this is done by tapping/clicking out of the text field
  edit_blur() {
    app.resume_timer();
  }

  // Called when a text value has been manually set, it would typically
  // happen when the text field loses focus (blur)
  manual_edit() {
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the set humidity minus button is pressed
  shum_minus() {
    press_button();
    var mode = this.controls.mode;
    if (mode == 4)
    {
      // Only support heating for now
      // API and values are too ugly
      var values = ['0', '40', '45', '50', '100'];
      var index = values.indexOf(this.controls.shum);
      if (index > 0) {
        this.controls.shum = values[index - 1];
        this.command_to_send = true;
        app.reset_timer();
      }
    }
  }

  // Called when the set humidity plus button is pressed
  shum_plus() {
    press_button();
    var mode = this.controls.mode;
    if (mode == 4)
    {
      // Only support heating for now
      // API and values are too ugly
      var values = ['0', '40', '45', '50', '100'];
      var index = values.indexOf(this.controls.shum);
      if (index >= 0 && index < values.length - 1) {
        this.controls.shum = values[index + 1];
        this.command_to_send = true;
        app.reset_timer();
      }
    }
  }

  // Called when any mode button is pressed
  set_mode(mode) {
    this.controls.mode = mode;
    this.display_mode();
    this.controls.stemp = this.controls['dt' + mode];
    this.controls.shum = this.controls['dh' + mode];
    this.command_to_send = true;
    app.reset_timer();
  }
}

// This is the Vue.js application that handles Aircon state changes and polling logic
var app = new Vue({
  el: '#app',
  data: {
    // The "Last refreshed" message at the top
    message: '',

    // This is the poll timer that gets reset, paused or restarted during UI interactions
    timer: null,

    // The combined A/C units consumption value
    consumption: '-',

    // Set the list of aircon units to control below
    // here /192.168.1.101 points to a reverse proxy
    // pointing to the actual wifi controller at the
    // same address at the path itself
    aircon: [
      new Aircon(0, '/192.168.1.101'),
      new Aircon(1, '/192.168.1.102')
    ]
  },
  // Called when the html #app element has been bound to the Vue instance
  mounted: function() {
    this.init_info();
    this.load_data();
    this.reset_timer();
    document.onfullscreenchange = function() {
      if (document.fullscreenElement) {
        document.getElementById('fullscreen').classList.add('hidden');
        document.getElementById('fullscreen-exit').classList.remove('hidden');
      } else {
        document.getElementById('fullscreen').classList.remove('hidden');
        document.getElementById('fullscreen-exit').classList.add('hidden');
      }
    }
  },
  methods: {
    // Load some wifi controller info and set the A/C name
    init_info: function() {
      var vm = this;
      for (index = 0; index < vm.aircon.length; index++) {
        (function(i) {
          aircon_api_get(vm.aircon[i].host, '/common/basic_info')
          .then(function([secs, map]) {
            vm.aircon[i].info = map;
            vm.aircon[i].name = decodeURI(vm.aircon[i].info.name);
          })
        })(index)
      }
    },
    // Called each time the poll timer has expired.
    // All A/C units' sensors and controls are polled concurrently
    // If a given A/C has commands to send, a set is performed and
    // a get follows after the response is received
    // After each get response is received, the matching UI elements are updated
    load_data: function() {
      var vm = this;
      for (index = 0; index < vm.aircon.length; index++) {
        (function(i) {
          var cl = document.getElementById('dot' + vm.aircon[i].num).classList;
          cl.remove('pulse-green', 'pulse-red');
          var p_sensors = aircon_api_get(vm.aircon[i].host, '/aircon/get_sensor_info')
          .then(function([secs, map]) {
            vm.aircon[i].sensors = map;
            vm.update_consumption();
            return secs;
          });
          var p;
          if (vm.aircon[i].command_to_send) {
            vm.aircon[i].command_to_send = false;
            var c = vm.aircon[i].controls;
            settings = {pow: c.pow, mode: c.mode, stemp: c.stemp, shum: c.shum};
            p = aircon_api_set(vm.aircon[i].host, '/aircon/set_control_info', settings);
          } else {
            p = Promise.resolve({});
          }
          var p_controls = p
          .then(function(x) {
            return aircon_api_get(vm.aircon[i].host, '/aircon/get_control_info')
          })
          .then(function([secs, map]) {
            if (vm.aircon[i].command_to_send) {
              // Ignore the response if we have already pressed a button to avoid bumping the display
              return secs;
            }
            var num_temp = Number(map.stemp);
            map.stemp = Number.isNaN(num_temp) ? '-' : num_temp;
            if (map.shum.toLowerCase() == 'continue') {
              map.shum = '100';
            }
            vm.aircon[i].controls = map;
            if (vm.aircon[i].controls.pow === '0') {
              vm.aircon[i].display_off();
            } else {
              vm.aircon[i].display_on();
            }
            vm.aircon[i].display_mode();
            return secs;
          })
          Promise.all([p_sensors, p_controls])
          .then(function(secs_sensors, secs_controls) {
            if (Math.max(secs_sensors, secs_controls) > 10) {
              return;
            }
            var cl = document.getElementById('dot' + vm.aircon[i].num).classList;
            cl.add('pulse-green');
            var d = new Date();
            vm.message = 'Last refreshed: ' +
            ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()] + ' ' +
            d.getDate() + '/' +
            (d.getMonth() + 1).toString().padStart(2, '0') + ' ' +
            d.getHours() + ':' +
            d.getMinutes().toString().padStart(2, '0') + ':' +
            d.getSeconds().toString().padStart(2, '0');
          })
          .catch(function(err) {
            var cl = document.getElementById('dot' + vm.aircon[i].num).classList;
            cl.add('pulse-red');
          });
        })(index)
      }
    },
    update_consumption: function() {
      this.consumption = app.aircon.reduce(
          (sum, ac) => sum + Number(ac.sensors.cmpfreq || 0) * 20 + (ac.controls.shum != '0' ? 200 : 0)
        , 0) + 'W';
    },
    stop_timer: function() {
      clearInterval(this.timer);
    },
    resume_timer: function() {
      this.timer = setInterval(() => {
        this.load_data();
      }, 3000);
    },
    reset_timer: function() {
      this.stop_timer();
      this.resume_timer();
    },
    enter_fullscreen: function() {
      document.documentElement.requestFullscreen();
    },
    exit_fullscreen: function() {
      document.exitFullscreen();
    }
  }
})
