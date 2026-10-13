/*
Photoshop Batch Processing Script with Optimized PNG Save Options
*/

// Main function
function processImages() {
    try {
        // Select source folder
        var sourceFolder = Folder.selectDialog("Select source folder with images");
        if (!sourceFolder) return;
        
        // Select destination folder
        var destFolder = Folder.selectDialog("Select destination folder for processed images");
        if (!destFolder) return;
        
        // Get all image files
        var files = sourceFolder.getFiles();
        var processedCount = 0;
        
        for (var i = 0; i < files.length; i++) {
            var file = files[i];
            
            // Check if it's an image file
            if (isImageFile(file)) {
                try {
                    processSingleImage(file, destFolder);
                    processedCount++;
                } catch (e) {
                    alert("Error processing file: " + file.name + "\n" + e.toString());
                }
            }
        }
        
        alert("Processing complete!\nProcessed files: " + processedCount);
        
    } catch (e) {
        alert("Script error: " + e.toString());
    }
}

// Check if file is supported image format
function isImageFile(file) {
    var extensions = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".psd"];
    var name = file.name.toLowerCase();
    
    for (var j = 0; j < extensions.length; j++) {
        if (name.indexOf(extensions[j]) != -1) {
            if (name.substring(name.length - extensions[j].length) == extensions[j]) {
                return true;
            }
        }
    }
    return false;
}

// Process single image file
function processSingleImage(file, destFolder) {
    var baseName = getBaseName(file.name);
    
    // Process JPG version - открываем файл заново для каждой версии
    processJPGVersion(file, destFolder, baseName);
    
    // Process PNG version - открываем файл заново для каждой версии  
    processPNGVersion(file, destFolder, baseName);
}

// Process JPG version - отдельная функция с открытием файла
function processJPGVersion(file, destFolder, baseName) {
    var doc = app.open(file);
    
    try {
        // Convert to RGB 8-bit
        doc.changeMode(ChangeMode.RGB);
        if (doc.bitsPerChannel != BitsPerChannelType.EIGHT) {
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        }
        
        // Resize to 600px on longer side
        resizeImage(doc, 600);
        
        // Save JPG with intelligent quality control
        saveJPGWithSmartQuality(doc, destFolder, baseName, 170);
        
    } finally {
        // Close without saving
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
}

// Process PNG version - отдельная функция с открытием файла
function processPNGVersion(file, destFolder, baseName) {
    var doc = app.open(file);
    
    try {
        // Convert to RGB 8-bit
        doc.changeMode(ChangeMode.RGB);
        if (doc.bitsPerChannel != BitsPerChannelType.EIGHT) {
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        }
        
        // Resize to 500px on longer side
        resizeImage(doc, 500);
        
        // Save with complete PNG/JPG fallback logic
        save500pxWithCompleteFallback(doc, destFolder, baseName);
        
    } finally {
        // Close without saving
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
}

// Resize image to specified size on longer side
function resizeImage(doc, targetSize) {
    var width = doc.width.value;
    var height = doc.height.value;
    var longerSide = Math.max(width, height);
    
    if (longerSide > targetSize) {
        var scale = targetSize / longerSide;
        var newWidth = Math.round(width * scale);
        var newHeight = Math.round(height * scale);
        
        doc.resizeImage(
            UnitValue(newWidth, "px"), 
            UnitValue(newHeight, "px"), 
            doc.resolution, 
            ResampleMethod.BICUBIC
        );
    }
}

// Smart JPG quality control - finds the BEST quality within size limit
function saveJPGWithSmartQuality(doc, destFolder, baseName, maxKB) {
    var jpgFile = new File(destFolder + "/" + baseName + ".jpg");
    var maxSize = maxKB * 1024;
    
    // First, test with high quality to see if we even need to reduce
    var testQuality = 12;
    var testSize = getEstimatedFileSize(doc, testQuality);
    
    if (testSize <= maxSize) {
        // Great! High quality fits, use it
        saveJPGWithQuality(doc, jpgFile, testQuality);
        return;
    }
    
    // If high quality doesn't fit, find optimal quality
    var optimalQuality = findOptimalQuality(doc, maxSize);
    
    // Save with found quality
    saveJPGWithQuality(doc, jpgFile, optimalQuality);
}

// Get estimated file size for given quality
function getEstimatedFileSize(doc, quality) {
    var tempFile = new File(Folder.temp + "/temp_size_check.jpg");
    
    var options = new JPEGSaveOptions();
    options.quality = quality;
    options.embedColorProfile = true;
    options.formatOptions = FormatOptions.OPTIMIZEDBASELINE;
    options.matte = MatteType.NONE;
    
    doc.saveAs(tempFile, options, true);
    var fileSize = tempFile.length;
    
    // Clean up
    if (tempFile.exists) {
        tempFile.remove();
    }
    
    return fileSize;
}

// Find optimal quality - starts from HIGH and goes down until size fits
function findOptimalQuality(doc, maxSize) {
    var qualities = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1];
    
    for (var i = 0; i < qualities.length; i++) {
        var quality = qualities[i];
        var estimatedSize = getEstimatedFileSize(doc, quality);
        
        if (estimatedSize <= maxSize) {
            return quality; // This quality fits, use it
        }
    }
    
    // If nothing fits, use quality 8
    return 8;
}

// Save JPG with specific quality
function saveJPGWithQuality(doc, jpgFile, quality) {
    var options = new JPEGSaveOptions();
    options.quality = quality;
    options.embedColorProfile = true;
    options.formatOptions = FormatOptions.OPTIMIZEDBASELINE;
    options.matte = MatteType.NONE;
    
    doc.saveAs(jpgFile, options, true);
}

// Complete PNG/JPG fallback logic with OPTIMIZED PNG SETTINGS
function save500pxWithCompleteFallback(doc, destFolder, baseName) {
    var maxSize = 500 * 1024; // 500 KB in bytes
    var outputFile;
    
    // Save original state for restoration between attempts
    var originalState = doc.activeHistoryState;
    
    try {
        // STEP 1: Try optimized PNG with medium compression first (better chance of success)
        outputFile = new File(destFolder + "/" + baseName + ".png");
        var pngOptions = new PNGSaveOptions();
        pngOptions.interlaced = false;  // No interlacing - smaller file size
        pngOptions.compression = 6;     // Medium compression (better balance)
        
        doc.saveAs(outputFile, pngOptions, true);
        
        if (outputFile.length <= maxSize) {
            return true; // Success - optimized PNG fits
        }
        
        // Restore original document state before next attempt
        doc.activeHistoryState = originalState;
        
        // STEP 2: Try PNG with maximum compression
        pngOptions.compression = 9; // Maximum compression
        pngOptions.interlaced = false;
        
        doc.saveAs(outputFile, pngOptions, true);
        
        if (outputFile.length <= maxSize) {
            return true; // Success - maximum compression PNG fits
        }
        
        // Restore original document state before next attempt
        doc.activeHistoryState = originalState;
        
        // STEP 3: Try PNG with no compression but reduced color depth if possible
        // First, check if we can optimize the image for smaller PNG
        optimizeImageForPNG(doc);
        
        pngOptions.compression = 0;
        pngOptions.interlaced = false;
        
        doc.saveAs(outputFile, pngOptions, true);
        
        if (outputFile.length <= maxSize) {
            return true; // Success - optimized image with no compression fits
        }
        
        // Restore original document state before JPG attempts
        doc.activeHistoryState = originalState;
        
        // STEP 4: All PNG attempts failed, switch to JPG
        if (outputFile.exists) {
            outputFile.remove(); // Remove the oversized PNG
        }
        
        // JPG quality progression from highest to lowest
        var jpgQualities = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1];
        
        for (var i = 0; i < jpgQualities.length; i++) {
            var quality = jpgQualities[i];
            outputFile = new File(destFolder + "/" + baseName + "_500.jpg");
            
            // Test this quality level
            var testSize = getEstimatedFileSize(doc, quality);
            
            if (testSize <= maxSize) {
                // This quality fits, save it
                saveJPGWithQuality(doc, outputFile, quality);
                return true;
            }
        }
        
        // If nothing fits, use the last quality (lowest)
        saveJPGWithQuality(doc, outputFile, 1);
        return false;
        
    } catch(e) {
        // Restore state on error
        doc.activeHistoryState = originalState;
        throw e;
    }
}

// Optimize image for smaller PNG file size
function optimizeImageForPNG(doc) {
    try {
        // Flatten the image if it has layers
        if (doc.layers.length > 1) {
            doc.flatten();
        }
        
        // Convert to RGB if it's in another color mode
        if (doc.mode != DocumentMode.RGB) {
            doc.changeMode(ChangeMode.RGB);
        }
        
        // Ensure 8-bit depth
        if (doc.bitsPerChannel != BitsPerChannelType.EIGHT) {
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        }
        
        // Remove alpha channel if present and not needed
        try {
            var alphaChannels = doc.channels;
            var hasAlpha = false;
            for (var i = 0; i < alphaChannels.length; i++) {
                if (alphaChannels[i].kind == ChannelType.MASKEDAREA || alphaChannels[i].kind == ChannelType.SELECTEDAREA) {
                    hasAlpha = true;
                    break;
                }
            }
            
            // If there's only one alpha channel (the default transparency), consider removing it
            if (hasAlpha && alphaChannels.length <= 3) { // RGB + 1 alpha
                // Check if the image actually uses transparency
                var hasTransparency = false;
                try {
                    doc.selection.selectAll();
                    var bounds = doc.selection.bounds;
                    var samplePoint = [bounds[0].value + 10, bounds[1].value + 10];
                    var sampleColor = doc.colorSamplers[0];
                    // If we can sample without error, there might be transparency
                    hasTransparency = true;
                } catch(e) {
                    // No transparency, safe to remove alpha channel
                    for (var j = alphaChannels.length - 1; j >= 3; j--) {
                        alphaChannels[j].remove();
                    }
                }
            }
        } catch(e) {
            // Ignore errors in alpha channel processing
        }
        
    } catch(e) {
        // Ignore optimization errors and continue
    }
}

// Get base name without extension
function getBaseName(filename) {
    var dotIndex = filename.lastIndexOf(".");
    if (dotIndex == -1) return filename;
    return filename.substring(0, dotIndex);
}

// Run the script
processImages();