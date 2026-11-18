
var addClickCallbackToButtons = function(plugin){
    $("#"+plugin).on("click", "."+plugin+"Button", function(event){
        var appliances  = [];
        var credentials = [];
        $.each(environment.appliances, function(index, appliance){
            if($("input[type='checkbox'][name='"+appliance.hostname+"']").is(":checked")){
                appliances.push(encodeURIComponent(appliance.hostname));
                credentials.push(encodeURIComponent(xor.encode(appliance.credentials, xor.encode(getCookie("9x4h/mmek/j.ahba.ckhafn"), "_"))));
            }
        });

        target = $(event.target);
        callable = target.attr("value").replace(/ /g, "_");
        data = {
            'callable': callable,
            'appliances[]': appliances,
            'credentials[]': credentials}

        var uri = plugin.split( "." )[2];
        $.get('/'+uri, data, function(data){
            $("#"+plugin+"Form").html(data);
        });
    });

    $("#"+plugin).on("click", ".output_close", function( event ){
        event.preventDefault();
        target = $( event.target );
        div = target.parent().parent();
        div.remove();
    });

    $( "#"+plugin ).on( "click", ".output_history", function( event ){
        event.preventDefault();
        target = $( event.target );
        parent = target.parent().parent();
        id = parent.find( ".hidden" ).attr( "id" );
        data = {"id": id}
        $.download("/download_history", data, "POST")
    } );

    $( "#" + plugin ).on( "click", ".help", function( event ){
        var target = $( event.target );
        var help_content = target.parent().find( ".help_content" );
        help_content.toggle();
    });
}

var addHandlerForFileUploads = function(plugin){
    $("#mast\\.datapower\\."+plugin+"Form").on("change", "input[type='file']",function(event){
        var target = $(event.target);
        var form = $(target.parent());
        var div = form.parent();
        var id = div.attr("id");
        var iframe = div.find("iframe[name^=_"+plugin+"]");
        $(document).trigger("ajaxStart");
        iframe.on("load", function(event){
            $(document).trigger("ajaxStop");
            var value = $.parseJSON($(iframe.contents().children()).find("body").find("pre").html()).filename;
            console.log( "filename: " + value );
            var hidden = $("#mast\\.datapower\\."+plugin+"Form").find("input[type='hidden'][name='"+id+"']");
            hidden.val(value);
        });
        form.trigger("submit");
    });
};

var addHandlerForFormSubmission = function(plugin){
    console.log("Adding handler for plugin " + plugin);
    $("#mast\\.datapower\\."+plugin+"Form").on("click", "#"+plugin+"FormSubmit", function(event){

        console.log("Form submission from " + plugin + "detected");
        var appliances  = [];
        var credentials = [];
        $.each(environment.appliances, function(index, appliance){
            if($("input[type='checkbox'][name='"+appliance.hostname+"']").is(":checked")){
                appliances.push(encodeURIComponent(appliance.hostname));
                credentials.push(encodeURIComponent(xor.encode(appliance.credentials, xor.encode(getCookie("9x4h/mmek/j.ahba.ckhafn"), "_"))));
            }
        });

        target = $(event.target);
        parentForm = target.parent();
        data = 'callable='+encodeURIComponent(parentForm.attr("name")) + '&appliances[]=';
        data += appliances.join('&appliances[]=') + '&credentials[]=';
        data += credentials.join('&credentials[]=');
        parentForm.find("[name]:not([name^=_])").each(function(index, elem){
            elem = $(elem);
            if (elem.attr("type")=="checkbox"){
                data += "&"+elem.attr("name")+"="+elem.is(":checked");
            }else{
                data += "&"+elem.attr("name")+"="+encodeURIComponent(elem.val());
            }
        });

        parentForm.find(".multiTextOuter").each(function(index, elem){
            elem = $(elem);
            id = elem.attr("id");
            selection = elem.find(".multiTextCurrent");
            choices = selection.html().replace("<i>Current selection: </i>", "");
            choices = choices.replace(/<span class="multiTextSelection bordered">/g, "");
            choices = choices.replace(/<span class="multiTextSelectionCancel"> X <\/span><\/span>/g, " ");
            choices = choices.split(" ");
            $.each(choices, function(index, value){
                if (/[a-zA-Z0-9\.]+/.test(value)){
                    data += "&"+encodeURIComponent(id)+"[]="+encodeURIComponent(value);
                }
            });
        });

        parentForm.find(".multiSelectOuter").each(function(index, elem){
            elem = $(elem);
            id = elem.attr("id");
            selection = elem.find(".multiSelectCurrent");
            choices = selection.html().replace("<i>Current selection: </i>", "");
            choices = choices.replace(/<span class="multiSelectSelection bordered">/g, "");
            choices = choices.replace(/<span class="multiSelectSelectionCancel"> X <\/span><\/span>/g, " ");
            choices = choices.split(" ");
            $.each(choices, function(index, value){
                if (/[a-zA-Z0-9\.]+/.test(value)){
                    data += "&"+encodeURIComponent(id)+"[]="+encodeURIComponent(value);
                }
            });
        });

        var uri = plugin;
        console.log("submitting request");
        $.post('/'+uri, data, function(data){
            console.log("Received response");
            console.log("#mast\\.datapower\\."+plugin+"OutputResults");
            // The response is the output of a command that could not be
            // queued, or the box of a job that the browser must follow.
            var added = $($.parseHTML($.trim(data)));
            $("#mast\\.datapower\\."+plugin+"OutputResults").prepend(added);
            added.filter(".job_pending").each(function(index, box){
                followJob($(box));
            });
        }).fail(function(xhr){
            // Without this, a request that fails shows nothing at all
            var message = $("<div>").addClass("width-100 output").text(
                "The server could not queue the command (HTTP " + xhr.status + ").");
            $("#mast\\.datapower\\."+plugin+"OutputResults").prepend(message);
        });
    });
};

// Fill the box of a job with its progress, then replace the box with
// the output of the job. The server answers at /jobs/<id>.
var followJob = function(box){
    var job = box.attr("data-job");
    var after = 0;
    var stopped = false;

    var show = function(state){
        $.each(state.events, function(index, message){
            box.find(".job_progress").append(document.createTextNode(message + "\n"));
        });
        after = state.last_event;
        box.find(".job_status").text(
            state.cancel_requested && !state.finished ? "cancelling" : state.status);
        if (state.finished){
            stopped = true;
            box.replaceWith($.parseHTML($.trim(state.html)));
        }
    };

    var poll = function(){
        if (stopped || !$.contains(document.documentElement, box[0])){
            return;
        }
        $.ajax({url: "/jobs/" + job, data: {after: after}, dataType: "json", global: false})
            .done(function(state){
                show(state);
                setTimeout(poll, 1000);
            })
            .fail(function(xhr){
                if (xhr.status == 404){
                    box.find(".job_status").text("unknown job");
                    return;
                }
                // The server can be away for a moment
                setTimeout(poll, 5000);
            });
    };

    box.find(".job_cancel").on("click", function(event){
        event.preventDefault();
        $.ajax({url: "/jobs/" + job + "/cancel", type: "POST", dataType: "json", global: false})
            .done(show);
    });

    poll();
};
